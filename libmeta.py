"""ROM Library artwork and game information.

Stored once, in the library itself, so every machine sees the same covers without downloading them again:
    <library>/.library/meta/<system>.json            descriptions, dates, genres ... per game
    <library>/.library/media/<system>/<id>_<kind>.<ext>   cover, screenshot, logo
Each machine keeps a local copy of what it has shown (~/.emu_rom_library/media) and small thumbnails.

Sources, tried in this order for a game with no information yet:
    ES-DE        what ES-DE has already downloaded on this machine (gamelist.xml + downloaded_media)
    ScreenScraper  with the user's own account and developer ID, entered in Settings
    libretro     free box art and screenshots from thumbnails.libretro.com (no account, pictures only)
Only artwork and text are downloaded, never games.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import cloudsync
import romlibrary as L

try:
    from PIL import Image
except ImportError:          # the app still works; pictures are kept as downloaded
    Image = None

MEDIA_CACHE = L.APP_DIR / "media"
THUMB_CACHE = L.APP_DIR / "thumbs"
SS_CREDS_FILE = L.APP_DIR / "screenscraper.json"   # this machine only, never in the library or the repo
KINDS = ("cover", "screenshot", "logo")
MAX_SIZE = {"cover": (600, 900), "screenshot": (800, 600), "logo": (600, 300)}
USER_AGENT = "EmuSaveManager-ROMLibrary"
RETRY_DAYS = 30                # a source that found nothing isn't asked again for this long

# ---------------------------------------------------------------------------
# System tables
# ---------------------------------------------------------------------------
# ScreenScraper system ids (from ES-DE's screenscraper_platformid_map)
SS_SYSTEM = {
    "3do": 29, "amiga": 64, "arcade": 75, "mame": 75, "fbneo": 75, "atari2600": 26, "atari5200": 40,
    "atari7800": 41, "atarijaguar": 27, "atarijaguarcd": 171, "atarilynx": 28, "atomiswave": 53,
    "c64": 66, "dos": 135, "dreamcast": 23, "gamegear": 21, "gb": 9, "gba": 12, "gbc": 10, "gc": 13,
    "genesis": 1, "mastersystem": 2, "megacd": 20, "segacd": 20, "msx": 113, "n3ds": 17, "n64": 14,
    "nds": 15, "neogeo": 142, "neogeocd": 70, "nes": 3, "ngp": 25, "ngpc": 82, "pcengine": 31,
    "pcfx": 72, "pico8": 234, "ps2": 58, "ps3": 59, "ps4": 60, "psp": 61, "psvita": 62, "psx": 57,
    "saturn": 22, "scummvm": 123, "sega32x": 19, "sg-1000": 109, "snes": 4, "switch": 225,
    "virtualboy": 11, "wii": 16, "wiiu": 18, "wonderswan": 45, "wonderswancolor": 46, "xbox": 32,
    "xbox360": 33, "primehack": 16, "naomi": 75, "model2": 75, "model3": 75,
}
# libretro-thumbnails folder names
LIBRETRO_SYSTEM = {
    "3do": "The 3DO Company - 3DO", "atari2600": "Atari - 2600", "atari5200": "Atari - 5200",
    "atari7800": "Atari - 7800", "atarijaguar": "Atari - Jaguar", "atarilynx": "Atari - Lynx",
    "dreamcast": "Sega - Dreamcast", "fbneo": "FBNeo - Arcade Games", "gamecom": "Tiger - Game.com",
    "gamegear": "Sega - Game Gear", "gb": "Nintendo - Game Boy", "gba": "Nintendo - Game Boy Advance",
    "gbc": "Nintendo - Game Boy Color", "gc": "Nintendo - GameCube", "genesis": "Sega - Mega Drive - Genesis",
    "mame": "MAME", "arcade": "MAME", "mastersystem": "Sega - Master System - Mark III",
    "megacd": "Sega - Mega-CD - Sega CD", "segacd": "Sega - Mega-CD - Sega CD", "msx": "Microsoft - MSX",
    "n3ds": "Nintendo - Nintendo 3DS", "n64": "Nintendo - Nintendo 64", "nds": "Nintendo - Nintendo DS",
    "neogeo": "SNK - Neo Geo", "neogeocd": "SNK - Neo Geo CD", "nes": "Nintendo - Nintendo Entertainment System",
    "ngp": "SNK - Neo Geo Pocket", "ngpc": "SNK - Neo Geo Pocket Color",
    "pcengine": "NEC - PC Engine - TurboGrafx 16", "pcfx": "NEC - PC-FX", "ps2": "Sony - PlayStation 2",
    "ps3": "Sony - PlayStation 3", "ps4": "Sony - PlayStation 4", "psp": "Sony - PlayStation Portable",
    "psvita": "Sony - PlayStation Vita", "psx": "Sony - PlayStation", "saturn": "Sega - Saturn",
    "sega32x": "Sega - 32X", "sg-1000": "Sega - SG-1000", "snes": "Nintendo - Super Nintendo Entertainment System",
    "virtualboy": "Nintendo - Virtual Boy", "wii": "Nintendo - Wii", "wiiu": "Nintendo - Wii U",
    "wonderswan": "Bandai - WonderSwan", "wonderswancolor": "Bandai - WonderSwan Color",
    "xbox": "Microsoft - Xbox", "xbox360": "Microsoft - Xbox 360", "naomi": "Sega - Naomi",
}


def media_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def year_of(date: str) -> str:
    m = re.search(r"(19|20)\d\d", date or "")
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# Pictures
# ---------------------------------------------------------------------------
def normalise_image(data: bytes, kind: str):
    """Shrink a downloaded picture to a sensible size; returns (bytes, ext).  Without Pillow, as is."""
    ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if data[:3] == b"\xff\xd8\xff" else ".img"
    if Image is None:
        return data, ext
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa - not a picture we can read
        return None, None
    im.thumbnail(MAX_SIZE.get(kind, (800, 800)))
    out = io.BytesIO()
    if kind == "logo" or im.mode in ("RGBA", "LA", "P") and "transparency" in im.info or im.mode == "RGBA":
        im.save(out, "PNG", optimize=True)
        return out.getvalue(), ".png"
    im.convert("RGB").save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue(), ".jpg"


def thumbnail(src: Path, width: int, height: int) -> Path | None:
    """A PNG thumbnail (cached) that Tk can show; None if it can't be made."""
    if not src or not Path(src).is_file():
        return None
    dst = THUMB_CACHE / f"{Path(src).stem}_{width}x{height}.png"
    try:
        if dst.is_file() and dst.stat().st_mtime >= Path(src).stat().st_mtime:
            return dst
    except OSError:
        pass
    if Image is None:
        return Path(src) if str(src).lower().endswith(".png") else None
    try:
        im = Image.open(src)
        im.thumbnail((width, height))
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp")
        im.save(tmp, "PNG")
        os.replace(tmp, dst)
        return dst
    except Exception:  # noqa
        return None


log = logging.getLogger("esm")      # the apps' log file (applog.py)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
def _merge_system(data: dict, system: str, recs: dict):
    """Put one system file's records into data under the canonical system name and keys
    ('dc.json' from before 'DC' was known as Dreamcast joins 'dreamcast'; its own name wins ties)."""
    canon = L.canonical(system)
    into = data.setdefault(canon, {})
    for k, r in recs.items():
        k2 = L.rekey(k)
        if k2 not in into or system == canon:
            into[k2] = r


class MetaStore:
    """Game information and artwork, kept in the library and cached on this machine."""

    def __init__(self, library: L.Library):
        self.lib = library
        self.data = {}                  # system -> {key: record}
        self.lock = threading.RLock()
        self._staged = {}               # system -> {key: record}  waiting to be written to the library
        self._staged_files = []         # paths (relative to the staging dir) waiting to be uploaded
        self._stage_dir = Path(tempfile.mkdtemp(prefix="romlib-media-"))
        self.cache_dir = MEDIA_CACHE / media_id(library.location)
        self._work = queue.Queue()      # background writes to the library, done in order
        self._worker = None
        self._io_lock = threading.Lock()    # one write to the library at a time
        self.write_ok = True            # False once a background write failed (until the next flush)

    # -- reading -------------------------------------------------------------
    def load(self):
        """Read every system's information file from the library (small JSON files)."""
        # one rclone command for every system's file (one connection), not one per system
        tmp = Path(tempfile.mkdtemp(prefix="romlib-meta-"))
        data = {}
        try:
            rc, _out = self.lib.runner.capture(["copy", self.lib._meta("meta"), str(tmp),
                                                "--include", "*.json", "--max-depth", "1"], timeout=600)
            if rc != 0:
                return
            for f in sorted(tmp.glob("*.json")):
                d = L._read_json(f, {})
                if isinstance(d, dict):
                    _merge_system(data, f.stem, d)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        with self.lock:
            self.data = data
        self._save_local_copy()

    def load_local_copy(self) -> bool:
        """The last information seen, so the window can show it before the library is read."""
        d = L._read_json(self.cache_dir / "meta.json", None)
        if not isinstance(d, dict):
            return False
        data = {}
        for system, recs in d.items():
            if isinstance(recs, dict):
                _merge_system(data, system, recs)
        with self.lock:
            self.data = data
        return True

    def _save_local_copy(self):
        with self.lock:
            d = dict(self.data)
        try:
            L._write_json(self.cache_dir / "meta.json", d)
        except OSError:
            pass

    def get(self, key: str) -> dict:
        system = key.split("/", 1)[0]
        with self.lock:
            return self.data.get(system, {}).get(key, {})

    def has_info(self, key) -> bool:
        r = self.get(key)
        return bool(r.get("media") or r.get("desc"))

    def media_path(self, key: str, kind: str) -> Path | None:
        """This machine's copy of a picture, if it has one already."""
        rel = self.get(key).get("media", {}).get(kind)
        if not rel:
            return None
        p = self.cache_dir / rel
        return p if p.is_file() else None

    def fetch_media(self, keys, kinds=("cover",)):
        """Copy pictures from the library to this machine (one rclone call for the lot)."""
        want = []
        for k in keys:
            for kind in kinds:
                rel = self.get(k).get("media", {}).get(kind)
                if rel and not (self.cache_dir / rel).is_file():
                    want.append(rel)
        if not want:
            return 0
        fd, listfile = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(want) + "\n")
        try:
            self.lib.runner.capture(["copy", self.lib._meta("media"), str(self.cache_dir),
                                     "--files-from-raw", listfile, "--no-traverse", "--transfers", "4"],
                                    timeout=600)
        finally:
            os.unlink(listfile)
        return sum(1 for rel in want if (self.cache_dir / rel).is_file())

    # -- writing -------------------------------------------------------------
    def put(self, key: str, record: dict, pictures: dict | None = None, replace: bool = False):
        """Add information for a game; pictures = {kind: bytes}.  Call flush() to write to the library.
        Normally only fills in what's missing; replace=True overwrites (a match picked by hand)."""
        system = key.split("/", 1)[0]
        mid = media_id(key)
        media = dict(self.get(key).get("media", {}))
        for kind, data in (pictures or {}).items():
            if not data:
                continue
            data, ext = normalise_image(data, kind)
            if not data:
                continue
            rel = f"{system}/{mid}_{kind}{ext}"
            for p in (self._stage_dir / rel, self.cache_dir / rel):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            media[kind] = rel
            self._staged_files.append(rel)
        rec = {k: v for k, v in record.items() if v not in (None, "", [], {})}
        old = self.get(key)
        merged = dict(old)
        for k, v in rec.items():
            if k == "tried":
                merged["tried"] = dict(old.get("tried", {}), **v)
            elif replace or not merged.get(k) or k in ("source",):
                merged[k] = v
        if media:
            merged["media"] = media
        with self.lock:
            self.data.setdefault(system, {})[key] = merged
            self._staged.setdefault(system, {})[key] = merged

    def mark_tried(self, key: str, source: str):
        self.put(key, {"tried": {source: int(time.time())}})

    def tried_recently(self, key: str, source: str) -> bool:
        t = self.get(key).get("tried", {}).get(source, 0)
        return time.time() - t < RETRY_DAYS * 86400

    def pending(self) -> int:
        with self.lock:
            return sum(len(v) for v in self._staged.values())

    # -- writing to the library ------------------------------------------------
    def flush(self) -> bool:
        """Upload staged pictures, then merge staged information into the library's files.
        Waits for background writes first, so everything staged so far is in the library after."""
        self.wait()
        ok = self.write_ok
        ok &= self.upload_media()
        ok &= self.write_info()
        self.write_ok = True
        return ok

    def flush_async(self, info_systems=()):
        """Upload the staged pictures in the background, then (if asked) write those systems'
        information files ('*' = every staged system). Returns at once; wait() to finish."""
        self._submit(("media", None))
        if info_systems:
            self._submit(("info", None if info_systems == "*" else tuple(info_systems)))

    def wait(self):
        """Block until every background write has been done."""
        if self._worker is not None:
            self._work.join()

    def _submit(self, task):
        self._work.put(task)
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run_work, daemon=True)
            self._worker.start()

    def _run_work(self):
        while True:
            try:
                kind, arg = self._work.get(timeout=2)
            except queue.Empty:
                return
            try:
                t0 = time.monotonic()
                n = len(self._staged_files) if kind == "media" else sorted(self._staged) if arg is None else arg
                ok = self.upload_media() if kind == "media" else self.write_info(arg)
                log.info("artwork: %s %s in %.1f s%s (%d more waiting)",
                         "uploaded" if kind == "media" else "saved information for",
                         f"{n} picture(s)" if kind == "media" else ", ".join(n) or "nothing",
                         time.monotonic() - t0, "" if ok else f" - FAILED: {L._last_lines(self.lib.error or '')[:300]}",
                         self._work.qsize())
                if not ok:
                    self.write_ok = False
            except Exception as ex:  # noqa - keep going; the next flush tries again
                log.exception("artwork: a background save failed")
                self.write_ok = False
                self.lib.error = str(ex)
            finally:
                self._work.task_done()

    def upload_media(self) -> bool:
        """Upload the pictures staged so far (one rclone copy)."""
        with self._io_lock:
            with self.lock:
                files, self._staged_files = self._staged_files, []
            if not files:
                return True
            fd, listfile = tempfile.mkstemp(suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(set(files))) + "\n")
            try:
                rc, out = self.lib.runner.capture(["copy", str(self._stage_dir), self.lib._meta("media"),
                                                   "--files-from-raw", listfile, "--no-traverse",
                                                   "--transfers", "4"], timeout=1800)
            finally:
                os.unlink(listfile)
            if rc != 0:
                self.lib.error = L._last_lines(out)
                with self.lock:                       # try them again at the next upload
                    self._staged_files = files + self._staged_files
                return False
            with self.lock:
                again = set(self._staged_files)       # staged once more meanwhile: keep for next time
            for rel in set(files) - again:
                try:
                    (self._stage_dir / rel).unlink()
                except OSError:
                    pass
            return True

    def write_info(self, systems=None) -> bool:
        """Merge the staged information of these systems (None = all) into the library's files.
        Each system's file is read first, because another machine may have added games."""
        ok = True
        with self._io_lock:
            with self.lock:
                names = list(self._staged) if systems is None else [x for x in systems if x in self._staged]
                staged = {x: self._staged.pop(x) for x in names}
            for system, recs in staged.items():
                loc = self.lib._meta("meta", system + ".json")
                latest = self.lib._cat_json(loc, {})
                if not isinstance(latest, dict):
                    latest = {}
                for k, r in recs.items():
                    cur = latest.get(k, {})
                    merged = dict(cur, **{kk: vv for kk, vv in r.items() if kk not in ("tried", "media")})
                    merged["tried"] = dict(cur.get("tried", {}), **r.get("tried", {}))
                    merged["media"] = dict(cur.get("media", {}), **r.get("media", {}))
                    latest[k] = {kk: vv for kk, vv in merged.items() if vv}
                if self.lib._put_json(loc, latest):
                    with self.lock:
                        self.data[system] = dict(self.data.get(system, {}), **latest)
                else:
                    ok = False
                    with self.lock:                   # keep them for the next try
                        again = self._staged.setdefault(system, {})
                        for k, r in recs.items():
                            again.setdefault(k, r)
        self._save_local_copy()
        return ok


# ---------------------------------------------------------------------------
# Source: ES-DE's own downloads on this machine
# ---------------------------------------------------------------------------
ESDE_KINDS = {"cover": ("covers",), "screenshot": ("screenshots", "titlescreens"), "logo": ("marquees",)}


def esde_locations():
    """[(gamelists dir, downloaded_media dir)] that exist on this machine."""
    home = Path.home()
    gl_dirs = [home / "ES-DE" / "gamelists", home / ".emulationstation" / "gamelists"]
    if L.IS_WIN:
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        gl_dirs += [appdata / "EmuDeck/Emulators/ES-DE/ES-DE/gamelists", home / "ES-DE/gamelists"]
    media_dirs = [home / "ES-DE" / "downloaded_media", home / ".emulationstation" / "downloaded_media"]
    for r in cloudsync.emudeck_roots():
        media_dirs.insert(0, r / "tools" / "downloaded_media")     # where EmuDeck points ES-DE
        gl_dirs.append(r / "tools" / "EmulationStation-DE" / "ES-DE" / "gamelists")
    gls = [d for d in gl_dirs if d.is_dir()]
    mds = [d for d in media_dirs if d.is_dir()]
    out = []
    for g in gls or [None]:
        for m in mds or [None]:
            if g or m:
                out.append((g, m))
    return out


class EsdeSource:
    name = "es-de"
    label = "ES-DE"

    def __init__(self, locations=None):
        self.locations = locations if locations is not None else esde_locations()
        self._games = {}         # canonical system -> {lookup key: record}
        self._media = {}         # canonical system -> [media dirs for that system]
        for gl, md in self.locations:
            if gl:
                for sysdir in gl.iterdir():
                    f = sysdir / "gamelist.xml"
                    if f.is_file():
                        self._read_gamelist(L.canonical(sysdir.name), f)
            if md:
                for sysdir in md.iterdir():
                    if sysdir.is_dir():
                        self._media.setdefault(L.canonical(sysdir.name), []).append(sysdir)

    @property
    def available(self):
        return bool(self._games or self._media)

    def summary(self):
        n = sum(len({id(v) for v in d.values()}) for d in self._games.values())
        return f"{n} games described, pictures for {len(self._media)} systems"

    @staticmethod
    def _keys(path: str):
        p = path.replace("\\", "/")
        if p.startswith("./"):
            p = p[2:]
        stem = os.path.splitext(p)[0]
        return [p.lower(), stem.lower(), re.sub(r"\s+", " ", os.path.basename(stem)).lower()]

    def _read_gamelist(self, system, f):
        try:
            root = ET.parse(f).getroot()
        except (ET.ParseError, OSError):
            return
        d = self._games.setdefault(system, {})
        for g in root.iter("game"):
            path = (g.findtext("path") or "").strip()
            if not path:
                continue
            rating = g.findtext("rating") or ""
            try:
                rating = round(float(rating) * 100) if rating else ""
            except ValueError:
                rating = ""
            date = (g.findtext("releasedate") or "").strip()
            rec = {"title": (g.findtext("name") or "").strip(), "desc": (g.findtext("desc") or "").strip(),
                   "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}" if re.match(r"^\d{8}", date) else "",
                   "year": year_of(date), "developer": (g.findtext("developer") or "").strip(),
                   "publisher": (g.findtext("publisher") or "").strip(), "genre": (g.findtext("genre") or "").strip(),
                   "players": (g.findtext("players") or "").strip(), "rating": rating, "_path": path}
            for k in self._keys(path):
                d.setdefault(k, rec)

    def lookup(self, item: L.Item):
        rec = None
        for k in self._keys(item.path) + self._keys(item.title):
            rec = self._games.get(item.system, {}).get(k)
            if rec:
                break
        pictures = {}
        rel_stems = []
        if rec:
            rel_stems.append(os.path.splitext(rec["_path"].replace("\\", "/").lstrip("./"))[0])
        rel_stems += [item.path if item.is_dir else os.path.splitext(item.path)[0], item.title]
        for kind, folders in ESDE_KINDS.items():
            found = None
            for sysdir in self._media.get(item.system, []):
                for folder in folders:
                    for stem in rel_stems:
                        for ext in (".png", ".jpg", ".jpeg", ".webp"):
                            p = sysdir / folder / (stem + ext)
                            if p.is_file():
                                found = p
                                break
                        if found:
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                try:
                    pictures[kind] = found.read_bytes()
                except OSError:
                    pass
        if not rec and not pictures:
            return None
        out = {k: v for k, v in (rec or {}).items() if not k.startswith("_")}
        out["source"] = self.name
        return out, pictures


# ---------------------------------------------------------------------------
# Web helpers
# ---------------------------------------------------------------------------
class HttpError(Exception):
    def __init__(self, code, body=""):
        super().__init__(f"HTTP {code}")
        self.code, self.body = code, body


def _tls(url):
    if not url.startswith("https:"):
        return None
    import netssl
    return netssl.context()


def http_get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls(url)) as r:
            return r.read()
    except urllib.error.HTTPError as ex:
        try:
            body = ex.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa
            body = ""
        raise HttpError(ex.code, body) from None


# ---------------------------------------------------------------------------
# Source: libretro-thumbnails (free pictures, no account)
# ---------------------------------------------------------------------------
class LibretroSource:
    name = "libretro"
    label = "libretro thumbnails"
    BASE = "https://thumbnails.libretro.com"

    def __init__(self, get=http_get, base=None):
        self.get = get
        self.base = (base or self.BASE).rstrip("/")

    available = True

    @staticmethod
    def _safe(name):
        return re.sub(r'[&*/:`<>?\\|"]', "_", name)

    def _names(self, item):
        lead = os.path.basename(item.path)
        stem = lead if item.is_dir else os.path.splitext(lead)[0]
        names = [stem]
        no_disc = re.sub(r"\s*\((Disc|Disk|CD)\s*\d+[^)]*\)", "", stem, flags=re.I).strip()
        if no_disc != stem:
            names.append(no_disc)
        no_serial = re.sub(r"\s*\[[A-Z]{4}\d{5}\]", "", stem).strip()          # PS3 folders: 'Name [BLUS30443]'
        if no_serial != stem:
            names.append(no_serial)
        return list(dict.fromkeys(names))

    def lookup(self, item: L.Item):
        system = LIBRETRO_SYSTEM.get(item.system)
        if not system:
            return None
        pictures = {}
        for kind, folder in (("cover", "Named_Boxarts"), ("screenshot", "Named_Snaps"), ("logo", "Named_Logos")):
            for n in self._names(item):
                url = f"{self.base}/{urllib.parse.quote(system)}/{folder}/{urllib.parse.quote(self._safe(n))}.png"
                try:
                    pictures[kind] = self.get(url)
                    break
                except HttpError as ex:
                    if ex.code != 404:
                        raise
                except (urllib.error.URLError, OSError) as ex:
                    raise HttpError(0, str(ex)) from None
            if kind == "cover" and "cover" not in pictures:
                break                     # no box art: don't spend requests on the rest
        if not pictures:
            return None
        return {"source": self.name}, pictures


# ---------------------------------------------------------------------------
# Source: ScreenScraper (the user's account plus a developer ID, both entered in Settings)
# ---------------------------------------------------------------------------
class QuotaReached(Exception):
    pass


class SourceUnavailable(Exception):
    pass


def load_ss_creds() -> dict:
    d = L._read_json(SS_CREDS_FILE, {})
    return d if isinstance(d, dict) else {}


def save_ss_creds(d: dict):
    L._write_json(SS_CREDS_FILE, d)
    try:
        os.chmod(SS_CREDS_FILE, 0o600)
    except OSError:
        pass


def _ss_reason(body) -> str:
    """ScreenScraper's own short error text (it never contains the login)."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    text = " ".join(str(body or "").split())
    return text[:160] if text and not text.lstrip().startswith("<") else ""


def _ss_login_message(body) -> str:
    """Which part of the login ScreenScraper rejected, from its (French) error text."""
    reason = _ss_reason(body)
    low = reason.lower()
    if "développeur" in low or "developpeur" in low or "developer" in low or "devid" in low:
        what = "the developer ID or developer password"
    elif "utilisateur" in low or "user" in low or "ssid" in low:
        what = "your ScreenScraper username or password"
    else:
        what = "the developer ID and password, or your username and password"
    return (f"ScreenScraper didn't accept {what}. Retype them in Settings (copying and pasting can add "
            "hidden characters)." + (f" ScreenScraper said: “{reason}”" if reason else ""))


class ScreenScraperSource:
    name = "screenscraper"
    label = "ScreenScraper"
    BASE = "https://api.screenscraper.fr/api2"
    SOFTNAME = "EmuSaveManager"
    REGIONS = ("us", "wor", "ss", "eu", "jp")
    MEDIA = {"cover": ("box-2D",), "screenshot": ("ss", "sstitle"), "logo": ("wheel-hd", "wheel")}

    def __init__(self, creds: dict, get=http_get, base=None):
        # spaces at either end are never part of a login: they come from copying and pasting
        self.creds = {k: (v.strip() if isinstance(v, str) else v) for k, v in (creds or {}).items()}
        self.get = get
        self.base = (base or self.BASE).rstrip("/")
        self.requests_today = None
        self.max_per_day = None

    @property
    def available(self):
        c = self.creds
        return bool(c.get("devid") and c.get("devpassword") and c.get("user") and c.get("password"))

    def _url(self, endpoint, **params):
        c = self.creds
        q = {"devid": c.get("devid", ""), "devpassword": c.get("devpassword", ""), "softname": self.SOFTNAME,
             "output": "xml", "ssid": c.get("user", ""), "sspassword": c.get("password", "")}
        q.update({k: v for k, v in params.items() if v not in (None, "")})
        return f"{self.base}/{endpoint}?{urllib.parse.urlencode(q)}"

    def _call(self, endpoint, **params):
        try:
            body = self.get(self._url(endpoint, **params), timeout=60)
        except HttpError as ex:
            text = ex.body.lower()
            if ex.code == 404:
                return None
            if ex.code in (430, 431) or "quota" in text:
                raise QuotaReached("ScreenScraper's daily limit for your account is reached; "
                                   "the rest will be looked up tomorrow.") from None
            if ex.code == 403:
                raise SourceUnavailable(_ss_login_message(ex.body)) from None
            if ex.code in (401, 429, 423):
                raise SourceUnavailable("ScreenScraper is busy or closed to non-members right now; "
                                        "try again later.") from None
            reason = _ss_reason(ex.body)
            raise SourceUnavailable(f"ScreenScraper answered with an error ({ex.code})"
                                    + (f": “{reason}”" if reason else ".")) from None
        except (urllib.error.URLError, OSError) as ex:
            import netssl
            why = netssl.describe_error(ex)
            raise SourceUnavailable(f"Couldn't reach ScreenScraper: {why} ({ex})" if why
                                    else f"Couldn't reach ScreenScraper: {ex}") from None
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            text = body[:200].decode("utf-8", "replace").lower()
            if "quota" in text:
                raise QuotaReached("ScreenScraper's daily limit for your account is reached.") from None
            raise SourceUnavailable("ScreenScraper sent something unexpected.") from None
        user = root.find("ssuser")
        if user is not None:
            try:
                self.requests_today = int(user.findtext("requeststoday") or 0)
                self.max_per_day = int(user.findtext("maxrequestsperday") or 0) or None
            except ValueError:
                pass
        return root

    def check_login(self) -> str:
        """A short description of the account, or raises SourceUnavailable."""
        root = self._call("ssuserInfos.php")
        if root is None:
            raise SourceUnavailable("ScreenScraper didn't recognise the account.")
        u = root.find("ssuser")
        if u is None:
            raise SourceUnavailable("ScreenScraper didn't return account details.")
        return (f"Signed in as {u.findtext('id') or self.creds.get('user')}: "
                f"{u.findtext('requeststoday') or 0} of {u.findtext('maxrequestsperday') or '?'} requests used today")

    @staticmethod
    def _pick(parent, child, attr, prefs):
        if parent is None:
            return ""
        nodes = parent.findall(child)
        for p in prefs:
            for n in nodes:
                if (n.get(attr) or "").lower() == p and (n.text or "").strip():
                    return n.text.strip()
        for n in nodes:
            if (n.text or "").strip():
                return n.text.strip()
        return ""

    def _parse(self, jeu):
        langs = ("en", "wor")
        genres = jeu.find("genres")
        genre = ""
        if genres is not None:
            names = []
            for g in genres.findall("genre"):
                if (g.get("langue") or "") == "en" and (g.text or "").strip():
                    names.append(g.text.strip())
            genre = ", ".join(list(dict.fromkeys(names))[:3])
        note = jeu.findtext("note") or ""
        try:
            rating = round(float(note) * 5) if note else ""       # ScreenScraper rates out of 20
        except ValueError:
            rating = ""
        date = self._pick(jeu.find("dates"), "date", "region", self.REGIONS)
        rec = {"title": self._pick(jeu.find("noms"), "nom", "region", self.REGIONS),
               "desc": self._pick(jeu.find("synopsis"), "synopsis", "langue", langs),
               "date": date, "year": year_of(date),
               "developer": (jeu.findtext("developpeur") or "").strip(),
               "publisher": (jeu.findtext("editeur") or "").strip(),
               "genre": genre, "players": (jeu.findtext("joueurs") or "").strip(), "rating": rating,
               "ss_id": jeu.get("id") or "", "source": self.name}
        urls = {}
        medias = jeu.find("medias")
        if medias is not None:
            for kind, types in self.MEDIA.items():
                for t in types:
                    url = self._pick_media(medias, t)
                    if url:
                        urls[kind] = url
                        break
        return rec, urls

    def _pick_media(self, medias, mtype):
        nodes = [m for m in medias.findall("media") if m.get("type") == mtype and (m.text or "").strip()]
        for r in self.REGIONS:
            for m in nodes:
                if (m.get("region") or "") == r:
                    return m.text.strip()
        return nodes[0].text.strip() if nodes else ""

    def lookup(self, item: L.Item):
        sid = SS_SYSTEM.get(item.system)
        if not sid:
            return None
        lead = os.path.basename(item.path)
        lead_size = item.files.get(item.path, (item.size, 0))[0] if not item.is_dir else item.size
        root = self._call("jeuInfos.php", systemeid=sid, romtype="rom", romnom=lead, romtaille=lead_size)
        if root is None:
            return None
        jeu = root.find("jeu")
        if jeu is None:
            return None
        rec, urls = self._parse(jeu)
        pictures = {}
        for kind, url in urls.items():
            try:
                pictures[kind] = self.get(url, timeout=60)     # the URL carries the login: never logged
            except (HttpError, urllib.error.URLError, OSError):
                pass
        return rec, pictures

    def search(self, system: str, text: str):
        """Name search for picking the right game by hand: [(ss game id, title, year)]."""
        root = self._call("jeuRecherche.php", systemeid=SS_SYSTEM.get(system), recherche=text)
        if root is None:
            return []
        out = []
        jeux = root.find("jeux")
        for jeu in (jeux.findall("jeu") if jeux is not None else []):
            rec, _ = self._parse(jeu)
            if rec["title"]:
                out.append((jeu.get("id"), rec["title"], rec["year"]))
        return out

    def by_id(self, game_id):
        root = self._call("jeuInfos.php", gameid=game_id)
        if root is None or root.find("jeu") is None:
            return None
        rec, urls = self._parse(root.find("jeu"))
        pictures = {}
        for kind, url in urls.items():
            try:
                pictures[kind] = self.get(url, timeout=60)
            except (HttpError, urllib.error.URLError, OSError):
                pass
        return rec, pictures


# ---------------------------------------------------------------------------
# The background job that fills in missing information
# ---------------------------------------------------------------------------
class Scraper:
    """Looks up games that have no information yet, source by source, in a background thread.
    progress(done, total, message) is called from that thread."""
    FLUSH_EVERY = 25          # games between picture uploads when online sources are in use
    FLUSH_EVERY_LOCAL = 120   # ... when only ES-DE (pictures already on this machine)
    UPLOAD_SECONDS = 45       # upload at least this often
    INFO_SECONDS = 300        # a long system's information file is also saved this often

    def __init__(self, store: MetaStore, sources, keys, progress=lambda d, t, m: None, refresh=False):
        self.store = store
        self.sources = [s for s in sources if s.available]
        self.keys = list(keys)
        self.progress = progress
        self.refresh = refresh
        self.cancelled = False
        self.message = ""
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def cancel(self):
        self.cancelled = True

    def _needs(self, key):
        r = self.store.get(key)
        return self.refresh or not r.get("media", {}).get("cover") or not r.get("desc")

    def run(self):
        lib = self.store.lib
        todo = [k for k in self.keys if lib.items.get(k) and self._needs(k)]
        todo.sort(key=lambda k: k.split("/", 1)[0])      # system by system (stable within one)
        total, since_flush = len(todo), 0
        local_only = all(src.name == "es-de" for src in self.sources)
        every = self.FLUSH_EVERY_LOCAL if local_only else self.FLUSH_EVERY
        last_upload = last_info = time.monotonic()
        system = None
        dead = set()
        self.progress(0, total, f"Looking up {total} game(s)…")
        t_start = time.monotonic()
        log.info("artwork: looking up %d game(s) with %s%s", total, ", ".join(src.name for src in self.sources),
                 " (look up again)" if self.refresh else "")
        for i, key in enumerate(todo, 1):
            if i % 50 == 0:
                log.info("artwork: %d of %d after %.0f s (%s; %d picture(s) waiting to upload, %d save(s) queued)",
                         i, total, time.monotonic() - t_start, key.split("/", 1)[0],
                         len(self.store._staged_files), self.store._work.qsize())
            if self.cancelled:
                self.message = "Stopped."
                break
            item = lib.items[key]
            sys_now = key.split("/", 1)[0]
            if system is not None and sys_now != system:
                self.store.flush_async(info_systems=[system])     # a system is done: save its file once
                since_flush, last_upload, last_info = 0, time.monotonic(), time.monotonic()
            system = sys_now
            have = self.store.get(key)
            for src in self.sources:
                if src.name in dead:
                    continue
                if src.name != "es-de" and not self.refresh and self.store.tried_recently(key, src.name):
                    continue
                need_text = not have.get("desc")
                need_cover = not have.get("media", {}).get("cover")
                if not (need_text or need_cover):
                    break
                if src.name == "libretro" and not need_cover:
                    continue
                try:
                    res = src.lookup(item)
                except QuotaReached as ex:
                    dead.add(src.name)
                    self.message = str(ex)
                    log.warning("artwork: %s stopped: %s", src.name, ex)
                    continue
                except (SourceUnavailable, HttpError) as ex:
                    dead.add(src.name)
                    self.message = str(ex) if isinstance(ex, SourceUnavailable) else \
                        f"{src.label} couldn't be reached; skipped for now."
                    log.warning("artwork: %s stopped: %s", src.name, ex)
                    continue
                except Exception:  # noqa - one game's odd data mustn't stop the whole look-up
                    log.exception("artwork: %s failed on %s", src.name, key)
                    continue
                if res:
                    rec, pics = res
                    if not need_cover:
                        pics.pop("cover", None)
                    for kind in have.get("custom", []):          # pictures chosen by hand stay
                        pics.pop(kind, None)
                    self.store.put(key, dict(rec, tried={src.name: int(time.time())}), pics)
                    have = self.store.get(key)
                elif src.name != "es-de":
                    self.store.mark_tried(key, src.name)
            since_flush += 1
            now = time.monotonic()
            if since_flush >= every or now - last_upload >= self.UPLOAD_SECONDS:
                long_system = now - last_info >= self.INFO_SECONDS
                self.store.flush_async(info_systems=[system] if long_system else ())
                since_flush, last_upload = 0, now
                if long_system:
                    last_info = now
            self.progress(i, total, f"{item.title}")
        self.progress(total, total, "Saving to the library…")
        self.store.flush_async(info_systems="*")
        self.store.wait()
        if not self.store.write_ok:
            self.store.flush()                                    # one more try, in this thread
        if not self.message:
            self.message = f"Done: information for {sum(1 for k in todo if self.store.has_info(k))} of {total} game(s)."
        log.info("artwork: finished after %.0f s: %s", time.monotonic() - t_start, self.message)
        self.progress(total, total, self.message)
