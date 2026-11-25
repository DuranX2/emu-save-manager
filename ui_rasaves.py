"""
ui_rasaves.py - the RetroArch Save Manager: every game with RetroArch saves, shown with its box art,
grouped by system, controller-friendly like the console managers (built on ui_console.ConsoleBase).

Slot 1 is RetroArch itself, slot 2 the one-game backups kept in the backup folder. For each game
the panel shows its box art, system and core, the in-game save, and its save states with their
screenshots (cycling). From the menus:

  * back up and restore single games (each backup is dated; several can be kept per game)
  * find saves left behind when a game file was renamed, and rename them to the closest game file
  * tidy saves scattered by changed RetroArch settings to where RetroArch looks now
  * move saves to and from standalone emulators (DuckStation cards, .sav files, N64 save files)

Box art comes from RetroArch's own thumbnails and ES-DE's downloads on this machine, and (unless
turned off) from the libretro thumbnails server, cached in ~/.emu_save_manager/ra_art/.
All file work is in rasaves.py / raconvert.py.
"""
from __future__ import annotations

import hashlib
import io
import os
import queue
import threading
import time
import tkinter as tk
import urllib.parse
from pathlib import Path
from tkinter import filedialog

import raconvert as C
import rasaves as R
import romlibrary as L
from ui_console import ACCENT as ACCENT_TXT, ConsoleBase, Image, MARK

try:
    from PIL import ImageDraw, ImageFont
except Exception:  # noqa
    ImageDraw = ImageFont = None

ART_W, ART_H = 104, 100
BIG_ART = 150
SLIDE_TICKS = 40                         # about 2.6 s per screenshot
ART_DIR = Path.home() / ".emu_save_manager" / "ra_art"
ART_RETRY = 7 * 86400
THUMBS_BASE = "https://thumbnails.libretro.com"


def _size(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB" if n else "0 KB"


def _png(im):
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _when(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


# ---------------------------------------------------------------------------
# Box art, loaded off the UI thread (the worker only ever sees plain data and the queues)
# ---------------------------------------------------------------------------
def _art_worker(jobs: queue.Queue, done: queue.Queue, cache_dir: Path, get):
    while True:
        job = jobs.get()
        if job is None:
            return
        key, local, remote = job
        data = None
        for p in local:
            try:
                if p.is_file():
                    data = p.read_bytes()
                    break
            except OSError:
                pass
        if data is None and remote:
            system, names = remote
            tag = hashlib.sha1((system + "|" + "|".join(names)).encode("utf-8")).hexdigest()[:20]
            hit, miss = cache_dir / (tag + ".png"), cache_dir / (tag + ".none")
            try:
                if hit.is_file():
                    data = hit.read_bytes()
                elif not (miss.is_file() and time.time() - miss.stat().st_mtime < ART_RETRY):
                    for n in names:
                        url = (f"{THUMBS_BASE}/{urllib.parse.quote(system)}/Named_Boxarts/"
                               f"{urllib.parse.quote(R._safe_thumb(n))}.png")
                        try:
                            data = get(url)
                            break
                        except Exception as ex:  # noqa - 404 means try the next name
                            if getattr(ex, "code", None) != 404:
                                data = None
                                break
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    if data:
                        hit.write_bytes(data)
                    elif data is None:
                        miss.write_bytes(b"")
            except OSError:
                pass
        done.put((key, data))


class ArtLoader:
    def __init__(self, download=True, get=None, cache_dir=None):
        self.download = download
        self.jobs, self.done = queue.Queue(), queue.Queue()
        self.asked = set()
        if get is None:
            try:
                import libmeta
                get = libmeta.http_get
            except Exception:  # noqa
                get = None
        self.get = get
        self.thread = threading.Thread(target=_art_worker, daemon=True,
                                       args=(self.jobs, self.done, Path(cache_dir or ART_DIR), get or (lambda u: None)))
        self.thread.start()

    def request(self, key, local, remote):
        if key in self.asked:
            return
        self.asked.add(key)
        self.jobs.put((key, list(local), remote if (self.download and self.get) else None))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.done.get_nowait())
            except queue.Empty:
                return out

    def stop(self):
        self.jobs.put(None)


# ---------------------------------------------------------------------------
# Items shown in the grid
# ---------------------------------------------------------------------------
class GameItem:
    kind = "game"

    def __init__(self, g: R.RAGame, lib: R.Library):
        self.game = g
        self.name = g.key
        self.title = g.title
        self.system = g.system
        self.stem = g.stem
        self.orphan = R.is_orphan(lib, g)
        self.scattered = bool(R.scattered(lib, g))
        self.db_name = g.rom.db_name if g.rom else ""

    @property
    def art_key(self):
        return f"{self.system}|{self.stem.lower()}"


class BackupItem:
    kind = "backup"

    def __init__(self, b: R.RABackup):
        self.backup = b
        self.name = str(b.path)
        self.title = b.title
        self.system = b.system
        self.stem = b.stem
        self.orphan = self.scattered = False
        self.db_name = ""

    @property
    def art_key(self):
        return f"{self.system}|{self.stem.lower()}"


SHORT = {"gb": "Game Boy", "gbc": "GB Color", "gba": "GBA", "nds": "DS", "n64": "N64", "nes": "NES", "snes": "SNES",
         "psx": "PS1", "ps2": "PS2", "psp": "PSP", "genesis": "Genesis", "mastersystem": "Master System",
         "gamegear": "Game Gear", "pcengine": "PC Engine", "segacd": "Sega CD", "megacd": "Mega-CD", "sega32x": "32X",
         "saturn": "Saturn", "dreamcast": "Dreamcast", "atari2600": "2600", "atari5200": "5200", "atari7800": "7800",
         "atarilynx": "Lynx", "atarijaguar": "Jaguar", "ngp": "NGP", "ngpc": "NGP Color", "wonderswan": "WonderSwan",
         "wonderswancolor": "WS Color", "virtualboy": "Virtual Boy", "n3ds": "3DS", "gc": "GameCube", "wii": "Wii",
         "arcade": "Arcade", "fbneo": "FBNeo", "mame": "MAME", "neogeo": "Neo Geo", "": "Other"}


def short_name(system):
    return SHORT.get(system) or L.SYSTEMS.get(system, system)


def system_title(system):
    return L.SYSTEMS.get(system, system) if system else "Other games"


class SystemItem:
    """A system on the first screen: opens that system's games."""
    kind = "system"

    def __init__(self, system, games, n_backups):
        self.system = system
        self.name = "sys:" + system
        self.title = system_title(system)
        self.games = games                        # [GameItem]
        self.n_backups = n_backups
        self.stem, self.db_name = "", ""
        self.orphan = self.scattered = False
        self.n_orphans = sum(1 for g in games if g.orphan)
        self.n_scattered = sum(1 for g in games if g.scattered)

    @property
    def art_key(self):
        return "system|" + self.system


class BackupsTile:
    kind = "backups"
    name = "backups"
    title = "Backups"
    system = stem = db_name = ""
    orphan = scattered = False
    art_key = "system|backups"

    def __init__(self, backups):
        self.backups = backups


def _system_card(system: str, w: int, h: int, label=None, sub=None):
    """An original placeholder 'box': a coloured card with the system's name (and a line under it)."""
    name = label or (L.SYSTEMS.get(system, "?") if system else "?")
    hue = int(hashlib.md5((system or "?").encode()).hexdigest()[:2], 16)
    import colorsys
    r, g, b = (int(v * 255) for v in colorsys.hsv_to_rgb(hue / 255, 0.45, 0.55))
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((4, 2, w - 5, h - 3), radius=8, fill=(r, g, b, 255), outline=(230, 235, 255, 200), width=2)
    d.rounded_rectangle((10, 8, w - 11, h - 9), radius=5, outline=(255, 255, 255, 60), width=1)
    def load(size):
        for f in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "NotoSans-Bold.ttf"):
            try:
                return ImageFont.truetype(f, size)
            except Exception:  # noqa
                continue
        return ImageFont.load_default()
    words = name.split()
    for size in (max(12, w // 7), 12, 11, 10, 9):     # shrink until the longest word fits
        font = load(size)
        if all(d.textlength(wd, font=font) <= w - 16 for wd in words):
            break
    lines, cur = [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if d.textlength(t, font=font) > w - 16 and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = t
    lines.append(cur)
    y = h // 2 - 8 * len(lines) - (8 if sub else 0)
    for ln in lines[:4]:
        tw = d.textlength(ln, font=font)
        d.text(((w - tw) / 2, y), ln, fill=(255, 255, 255, 255), font=font)
        y += 16
    if sub:
        small = font
        try:
            small = ImageFont.truetype(getattr(font, "path", "DejaVuSans.ttf").replace("-Bold", ""), 11)
        except Exception:  # noqa
            pass
        tw = d.textlength(sub, font=small)
        d.rounded_rectangle(((w - tw) / 2 - 6, h - 26, (w + tw) / 2 + 6, h - 10), radius=7, fill=(0, 0, 0, 90))
        d.text(((w - tw) / 2, h - 25), sub, fill=(235, 240, 255, 255), font=small)
    return im


def _badge(im, symbol):
    im = im.copy()
    d = ImageDraw.Draw(im)
    r = 10
    x, y = im.width - r - 3, r + 3
    d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 196, 80, 255), outline=(40, 30, 0, 255))
    font = None
    for f in ("DejaVuSans-Bold.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(f, 13)
            break
        except Exception:  # noqa
            continue
    font = font or ImageFont.load_default()
    tw = d.textlength(symbol, font=font)
    d.text((x - tw / 2, y - 8), symbol, fill=(30, 20, 0, 255), font=font)
    return im


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------
class RAConsoleView(ConsoleBase):
    HEADING = "Saves"
    SUBHEADING = "RetroArch"
    TILE_ICON = ART_H
    EDGE_SWITCH = False

    def __init__(self, master, dialog):
        super().__init__(master)
        self.dialog = dialog
        self.lib = None
        self.backups = []
        self.mode = "systems"          # systems (the first screen) | games (one system) | backups
        self.cur = None                # the system shown in games mode ('' = other games)
        self.systems = []              # systems with saves or backups, in display order
        self.by_system = {}            # system -> [GameItem]
        self.pos = {}                  # where the cursor was, per screen
        self.warned = False
        self.art = {}                  # art key -> bytes | None (None: none found)
        self.loader = ArtLoader(dialog.download_art(), getattr(dialog, "http_get", None),
                                getattr(dialog, "art_cache", None))
        self.slide = 0
        self._slide_ticks = 0

    # ---- data ------------------------------------------------------------------
    def reload(self, rescan=True):
        if rescan or self.lib is None:
            try:
                self.lib = R.scan(self.dialog.settings(), self.dialog.rom_dirs())
                self.error[0] = ""
            except OSError as ex:
                self.lib, self.error[0] = None, f"Can't read RetroArch's saves: {ex}"
            try:
                self.backups = R.list_backups(self.dialog.backup_dir)
                self.error[1] = ""
            except OSError as ex:
                self.backups, self.error[1] = [], f"Can't read the backups: {ex}"
        games = [GameItem(g, self.lib) for g in (self.lib.games if self.lib else [])]
        self.by_system = {}
        for it in games:
            self.by_system.setdefault(it.system, []).append(it)
        names = set(self.by_system) | {b.system for b in self.backups}
        self.systems = sorted(names, key=lambda k: (k == "", system_title(k).lower()))
        for it in games:
            self._ask_art(it)
        self.slide = 0
        self.photos = {}
        self._build()

    def _build(self):
        if self.mode == "games" and self.cur not in self.by_system:
            self.mode = "systems"
        if self.mode == "systems":
            n_b = {}
            for b in self.backups:
                n_b[b.system] = n_b.get(b.system, 0) + 1
            tiles = [SystemItem(k, self.by_system.get(k, []), n_b.get(k, 0)) for k in self.systems
                     if self.by_system.get(k)]
            if tiles or self.backups:
                tiles.append(BackupsTile(self.backups))
            self.items[0] = tiles
            self.side = 0
        elif self.mode == "games":
            self.items[0] = list(self.by_system.get(self.cur, []))
            self.side = 0
        else:
            self.items[1] = [BackupItem(b) for b in self.backups]
            for it in self.items[1]:
                self._ask_art(it)
            self.side = 1
        self.clamp()
        self.redraw(full=True)

    # ---- moving between screens -------------------------------------------------------
    def _screen(self):
        return self.mode if self.mode != "games" else "games:" + str(self.cur)

    def _remember(self):
        self.pos[self._screen()] = (self.cursor[self.side], self.scroll[self.side])

    def _restore_pos(self, default=0):
        c, sc = self.pos.get(self._screen(), (default, 0))
        self.cursor[self.side], self.scroll[self.side] = c, sc

    def open_system(self, system):
        self._remember()
        self.mode, self.cur = "games", system
        self.side = 0
        self.marks[0].clear()
        self._restore_pos()
        self.on_cursor()
        self._build()

    def open_backups(self):
        self._remember()
        self.mode = "backups"
        self.side = 1
        self._restore_pos()
        self.on_cursor()
        self._build()

    def go_home(self):
        came_from = self.cur if self.mode == "games" else "backups"
        self._remember()
        self.mode = "systems"
        self.side = 0
        self.marks = [set(), set()]
        self._build()
        idx = next((i for i, it in enumerate(self.items[0])
                    if (it.kind == "system" and it.system == came_from) or
                    (it.kind == "backups" and came_from == "backups")), 0)
        self.cursor[0] = idx
        self.on_cursor()
        self.redraw()

    def act(self, action):
        if self.overlay is None and self.mode == "systems" and action == "mark":
            return
        super().act(action)

    def tab_items(self):
        if self.mode == "games":
            return [(short_name(k), k == self.cur) for k in self.systems if self.by_system.get(k)]
        return [("Systems" if self.mode == "systems" else "Backups", True)]

    def on_tab(self, i):
        if self.mode == "games":
            shown = [k for k in self.systems if self.by_system.get(k)]
            if 0 <= i < len(shown) and shown[i] != self.cur:
                self.open_system(shown[i])

    def on_prev_next(self, step):
        if self.mode != "games":
            return
        shown = [k for k in self.systems if self.by_system.get(k)]
        if len(shown) > 1:
            i = shown.index(self.cur) if self.cur in shown else 0
            self.open_system(shown[(i + step) % len(shown)])

    def on_back(self):
        if self.mode == "systems":
            self.main_menu()
        else:
            self.go_home()

    def _scope(self):
        """The games the menu's tools work on: this system's, or all of them on the first screen."""
        if self.mode == "games":
            return list(self.by_system.get(self.cur, []))
        return [it for lst in self.by_system.values() for it in lst]

    def _ask_art(self, it):
        if it.art_key in self.art:
            return
        settings = self.lib.settings if self.lib else R.Settings()
        local = R.local_art_candidates(settings, it.system, it.title, it.stem, it.db_name, self.dialog.esde_media())
        sysname = R.LIBRETRO_SYSTEM.get(it.system, "")
        remote = (sysname, R.art_names(it.title, it.stem)) if sysname else None
        self.loader.request(it.art_key, local, remote)

    def on_tick(self):
        changed = False
        for key, data in self.loader.poll():
            self.art[key] = data or None
            if data:
                self.photos = {k: v for k, v in self.photos.items() if k[1] != key}
                changed = True
        self._slide_ticks += 1
        if self._slide_ticks >= SLIDE_TICKS:
            self._slide_ticks = 0
            it = self.current()
            if it is not None and len(self._shots(it)) > 1:
                self.slide += 1
                changed = True
        return changed

    def on_cursor(self):
        self.slide, self._slide_ticks = 0, 0

    def on_stop(self):
        self.loader.stop()

    # ---- pictures --------------------------------------------------------------
    def _art_image(self, it):
        data = self.art.get(it.art_key)
        if data and Image is not None:
            try:
                im = Image.open(io.BytesIO(data))
                im.load()
                return im.convert("RGBA")
            except Exception:  # noqa
                return None
        return None

    def _fit(self, im, w, h):
        k = min(w / im.width, h / im.height)
        return im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)

    def tile_image(self, it):
        if it.kind in ("system", "backups"):
            key = ("home", it.art_key, len(getattr(it, "games", None) or getattr(it, "backups", [])))
            if key not in self.photos:
                try:
                    self.photos[key] = tk.PhotoImage(master=self.canvas, data=_png(self._home_tile(it, 80, ART_H)))
                except Exception:  # noqa
                    self.photos[key] = None
            return self.photos[key]
        badge = "?" if it.orphan else ("↔" if it.scattered else "")
        key = ("tile", it.art_key, badge, bool(self.art.get(it.art_key)))
        if key not in self.photos:
            ph = None
            if Image is not None:
                try:
                    im = self._art_image(it)
                    im = self._fit(im, ART_W, ART_H) if im is not None else _system_card(it.system, 74, ART_H)
                    if badge:
                        im = _badge(im, badge)
                    ph = tk.PhotoImage(master=self.canvas, data=_png(im))
                except Exception:  # noqa
                    ph = None
            self.photos[key] = ph
        return self.photos[key]

    def _big_art(self, it, size):
        key = ("big", it.art_key, size, bool(self.art.get(it.art_key)))
        if key not in self.photos:
            ph = None
            if Image is not None:
                try:
                    im = self._art_image(it)
                    im = self._fit(im, size, size) if im is not None else _system_card(it.system, size * 3 // 4, size)
                    ph = tk.PhotoImage(master=self.canvas, data=_png(im))
                except Exception:  # noqa
                    ph = None
            self.photos[key] = ph
        return self.photos[key]

    def _shots(self, it):
        """[(path of screenshot, caption)] for the item's save states."""
        out = []
        if it.kind == "game":
            g = it.game
            for st in g.states:
                th = g.thumb_for(st)
                if th is not None:
                    out.append((th.path, f"{R.slot_name(st.slot)} · {_when(st.mtime)}"))
        else:
            thumbs = [(p, slot if slot is not None else 0) for p, area, kind, slot in it.backup.files if kind == "thumb"]
            for p, slot in sorted(thumbs, key=lambda t: t[1] if t[1] >= 0 else 10_000):
                try:
                    when = _when(p.stat().st_mtime)
                except OSError:
                    when = ""
                out.append((p, R.slot_name(slot) + (f" · {when}" if when else "")))
        return out

    def _shot_photo(self, path, w, h):
        key = ("shot", str(path), w, h)
        if key not in self.photos:
            ph = None
            if Image is not None:
                try:
                    im = Image.open(path)
                    im.load()
                    im = self._fit(im.convert("RGB"), w, h)
                    ph = tk.PhotoImage(master=self.canvas, data=_png(im))
                except Exception:  # noqa
                    ph = None
            self.photos[key] = ph
        return self.photos[key]

    # ---- hooks -----------------------------------------------------------------
    def tab_label(self, side):
        return "RetroArch" if side == 0 else "Backups"

    def space_text(self, side):
        if self.mode == "systems":
            n = sum(1 for it in self.items[0] if it.kind == "system")
            total = sum(len(v) for v in self.by_system.values())
            return f"{n} system{'s' if n != 1 else ''} · {total} game{'s' if total != 1 else ''}"
        items = self.items[side]
        if self.mode == "games":
            return f"{system_title(self.cur)} · {len(items)} game{'s' if len(items) != 1 else ''}"
        return f"{len(items)} backup{'s' if len(items) != 1 else ''}"

    def empty_text(self, side):
        if self.mode == "backups":
            return ("No backups yet.\nOpen a system, choose a game and press Ⓐ ▸ Back up this game "
                    "to keep a copy of its saves and states here.")
        if self.lib is None or (self.lib.settings.saves_dir is None and self.lib.settings.states_dir is None):
            return ("RetroArch's saves weren't found.\nSet its Saves and States folders with "
                    "'Locations…' in the main window.")
        if self.mode == "games":
            return "No saves for this system."
        return "No RetroArch saves yet."

    def mark_key(self, it):
        return it.name

    def tile_title(self, it):
        if it.kind == "backup":
            same = sum(1 for o in self.items[1] if o.art_key == it.art_key)
            if same > 1 and it.backup.created:
                return f"{it.backup.created:%Y-%m-%d %H:%M}"
        return it.title

    def marked_text(self, n):
        return f"{n} marked"

    def footer(self):
        if self.mode == "systems":
            return [("A", "Open"), ("Y", "Menu")]
        if self.mode == "games":
            return [("A", "Options"), ("X", "Mark"), ("Y", "Menu"), ("B", "Systems"), ("LB/RB", "System")]
        return [("A", "Options"), ("X", "Mark"), ("Y", "Menu"), ("B", "Systems")]

    def on_view(self):
        if self.mode != "systems":
            self.go_home()

    def card_menu(self, side):
        pass

    def _home_tile(self, it, w, h):
        if it.kind == "backups":
            n = len(it.backups)
            return _system_card("backups", w, h, "Backups", f"{n} saved" if n else "none yet")
        n = len(it.games)
        return _system_card(it.system, w, h, short_name(it.system), f"{n} game{'s' if n != 1 else ''}")

    def _draw_home_info(self, it, box):
        c = self.canvas
        x0, y0, x1, y1 = box
        panel = self.panel_image(("ra",), [(34, 44, 92), (24, 30, 70), (16, 20, 48), (10, 12, 32)],
                                 x1 - x0, y1 - y0)
        if panel:
            c.create_image(x0, y0, image=panel, anchor="nw", tags="ui")
        pad = 16
        size = min(BIG_ART, (x1 - x0) // 3)
        key = ("homebig", it.art_key, size, len(getattr(it, "games", None) or getattr(it, "backups", [])))
        if key not in self.photos:
            try:
                self.photos[key] = tk.PhotoImage(master=self.canvas, data=_png(self._home_tile(it, size * 3 // 4, size)))
            except Exception:  # noqa
                self.photos[key] = None
        if self.photos[key]:
            c.create_image(x0 + pad, y0 + pad, image=self.photos[key], anchor="nw", tags="ui")
        tx = x0 + pad + size + 14
        tw = x1 - tx - pad
        y = y0 + pad
        c.create_text(tx, y, text=it.title, fill="#ffffff", font=self.f_title, anchor="nw", width=tw, tags="ui")
        y += self.f_title.metrics("linespace") * (2 if self.f_title.measure(it.title) > tw else 1) + 4
        if it.kind == "system":
            games = it.games
            n_states = sum(len(g.game.states) for g in games)
            rows = [f"{len(games)} game{'s' if len(games) != 1 else ''} · "
                    f"{n_states} save state{'s' if n_states != 1 else ''}"]
            if it.n_backups:
                rows.append(f"{it.n_backups} backup{'s' if it.n_backups != 1 else ''}")
            y = self.text_rows(tx, y, rows, tw, self.f_body, "#e6e9f8")
            warn = []
            if it.n_orphans:
                warn.append(f"? {it.n_orphans} with no game file")
            if it.n_scattered:
                warn.append(f"↔ {it.n_scattered} with files out of place")
            if warn:
                y = self.text_rows(tx, y, warn, tw, fill=MARK)
            y = max(y, y0 + pad + size + 16)
            recent = sorted(games, key=lambda g: -(g.game.modified.timestamp() if g.game.modified else 0))[:8]
            if recent:
                y = self.text_rows(x0 + pad, y, ["Played most recently"], x1 - x0 - 2 * pad, self.f_body, "#ffffff")
                y = self.text_rows(x0 + pad + 8, y, [
                    f"{g.title}" + (f"  ·  {g.game.modified:%Y-%m-%d}" if g.game.modified else "") for g in recent],
                    x1 - x0 - 2 * pad - 8, fill="#c9d0f0")
        else:
            bs = it.backups
            rows = [f"{len(bs)} backup{'s' if len(bs) != 1 else ''} of {len({b.key for b in bs})} "
                    f"game{'s' if len({b.key for b in bs}) != 1 else ''}"] if bs else ["No backups yet"]
            y = self.text_rows(tx, y, rows, tw, self.f_body, "#e6e9f8")
            y = max(y, y0 + pad + size + 16)
            y = self.text_rows(x0 + pad, y, [
                "Every game's backups, all systems together. A game's own backups are also in its Ⓐ menu."],
                x1 - x0 - 2 * pad, fill="#c9d0f0")
        c.create_text((x0 + x1) // 2, y1 - pad - 6, text="Ⓐ Open", fill=ACCENT_TXT, font=self.f_body,
                      anchor="s", tags="ui")

    def draw_info(self, it, box):
        if it.kind in ("system", "backups"):
            self._draw_home_info(it, box)
            return
        c = self.canvas
        x0, y0, x1, y1 = box
        panel = self.panel_image(("ra",), [(34, 44, 92), (24, 30, 70), (16, 20, 48), (10, 12, 32)],
                                 x1 - x0, y1 - y0)
        if panel:
            c.create_image(x0, y0, image=panel, anchor="nw", tags="ui")
        pad = 16
        size = min(BIG_ART, (x1 - x0) // 3)
        art = self._big_art(it, size)
        if art:
            c.create_image(x0 + pad, y0 + pad, image=art, anchor="nw", tags="ui")
        tx = x0 + pad + size + 14
        tw = x1 - tx - pad
        y = y0 + pad
        c.create_text(tx, y, text=it.title, fill="#ffffff", font=self.f_title, anchor="nw", width=tw, tags="ui")
        lines = max(1, min(3, int(self.f_title.measure(it.title) / max(tw, 1)) + 1))
        y += self.f_title.metrics("linespace") * lines + 4
        if it.kind == "game":
            g = it.game
            sysline = g.system_name + (f" · {g.core}" if g.core else "")
            y = self.text_rows(tx, y, [sysline], tw, self.f_body, "#e6e9f8")
            if it.orphan:
                y = self.text_rows(tx, y, ["No game file with this name", "Ⓨ ▸ Find saves with no game file"],
                                   tw, fill=MARK)
            elif it.scattered:
                y = self.text_rows(tx, y, ["Some files are where RetroArch",
                                           "no longer looks: Ⓐ ▸ Move them back"], tw, fill=MARK)
            elif g.rom is not None:
                y = self.text_rows(tx, y, [g.rom.path.name], tw, fill="#aab4e0")
        else:
            b = it.backup
            y = self.text_rows(tx, y, [b.system_name + (f" · {b.info.get('core')}" if b.info.get("core") else "")],
                               tw, self.f_body, "#e6e9f8")
            if b.created:
                y = self.text_rows(tx, y, [f"Backed up {b.created:%Y-%m-%d %H:%M}"], tw, fill="#d9def5")
        y = max(y, y0 + pad + size + 12)
        full = x1 - x0 - 2 * pad
        if it.kind == "game":
            g = it.game
            sr = sorted(g.srams, key=lambda f: -f.mtime)
            rows = []
            if sr:
                rows.append(f"In-game save · {_size(sr[0].size)} · {_when(sr[0].mtime)}"
                            + (f"  (+{len(sr) - 1} more file{'s' if len(sr) > 2 else ''})" if len(sr) > 1 else ""))
            else:
                rows.append("No in-game save")
            st = g.states
            if st:
                names = ", ".join(R.slot_name(s.slot) for s in st[:8]) + ("…" if len(st) > 8 else "")
                rows.append(f"{len(st)} save state{'s' if len(st) != 1 else ''}: {names}")
            else:
                rows.append("No save states")
            y = self.text_rows(x0 + pad, y, rows, full, self.f_body, "#e6e9f8")
        else:
            b = it.backup
            n_states = sum(1 for p, a, k, s in b.files if k == "state")
            n_sram = sum(1 for p, a, k, s in b.files if k == "sram")
            rows = [f"{n_sram} in-game save file{'s' if n_sram != 1 else ''}, "
                    f"{n_states} save state{'s' if n_states != 1 else ''} · {_size(b.size)}"]
            if b.info.get("note"):
                rows.append(b.info["note"])
            y = self.text_rows(x0 + pad, y, rows, full, self.f_body, "#e6e9f8")
        shots = self._shots(it)
        room = y1 - y - pad - 40
        if shots and room > 60:
            path, caption = shots[self.slide % len(shots)]
            ph = self._shot_photo(path, full, min(room, 260))
            if ph:
                c.create_image((x0 + x1) // 2, y + 8, image=ph, anchor="n", tags=("ui", "big"))
                y += ph.height() + 14
            c.create_text((x0 + x1) // 2, y, text=caption + (f"   ({self.slide % len(shots) + 1}/{len(shots)})"
                                                         if len(shots) > 1 else ""),
                          fill="#c9d0f0", font=self.f_small, anchor="n", tags="ui")

    # ---- shared helpers ----------------------------------------------------------
    def _close_retroarch(self):
        if self.warned:
            return True
        ok = self.prompts.askokcancel(
            "Close RetroArch first", "RetroArch writes saves while a game runs, so changes made now could be "
            "undone. Make sure RetroArch is closed, then choose OK.")
        self.warned = ok
        return ok

    def _report(self, rep, verb, noun="file"):
        self.marks[self.side].clear()
        self.reload()
        if rep is None:
            return
        if rep.refused:
            self.prompts.showinfo("Some saves weren't changed",
                                  "\n".join(f"• {what}: {why}" for what, why in rep.refused[:12]))
        n = len(rep.done)
        if n:
            self.toast(f"{verb} {n} {noun}{'s' if n != 1 else ''}")

    def _games(self):
        return [it.game for it in self.selection() if it.kind == "game"]

    # ---- menus -------------------------------------------------------------------
    def save_menu(self):
        sel = self.selection()
        if not sel:
            return
        it = sel[0]
        if it.kind == "system":
            self.open_system(it.system)
            return
        if it.kind == "backups":
            self.open_backups()
            return
        if it.kind == "backup":
            what = f"{len(sel)} backups" if len(sel) > 1 else f"{it.title} — {self.tile_title(it)}"
            opts = [("restore", "Restore to RetroArch" + (" (the newest of each game)" if len(sel) > 1 else "")),
                    ("delete", "Delete" + (" these backups" if len(sel) > 1 else " this backup")),
                    ("details", "Details"), ("cancel", "Cancel")]
            if len(sel) > 1:
                opts.remove(("details", "Details"))
            self.show_overlay(what, "", opts, lambda k: {"restore": self._restore, "delete": self._delete_backups,
                                                         "details": self.details}.get(k, lambda: None)())
            return
        games = [s.game for s in sel]
        what = f"{len(games)} games" if len(games) > 1 else f"{it.title} ({it.game.system_name})"
        opts = [("backup", "Back up " + ("these games" if len(games) > 1 else "this game"))]
        mine = [b for b in self.backups if b.key == it.game.key] if len(games) == 1 else []
        if mine:
            opts.append(("restore-one", f"Restore a backup… ({len(mine)})"))
        if len(games) == 1 and it.orphan:
            opts.append(("rename", "Rename to match a game file…"))
        if any(s.scattered for s in sel):
            opts.append(("tidy", "Move files back to where RetroArch looks now"))
        if len(games) == 1:
            kind = C.kind_for(it.game)
            if kind == "ps1":
                opts += [("ps1-out", "Copy memory card to DuckStation…"),
                         ("ps1-in", "Use a memory card from DuckStation…")]
            elif kind == "raw":
                opts += [("raw-out", "Export as .sav for a standalone emulator…"),
                         ("raw-in", "Import a .sav from a standalone emulator…")]
            elif kind == "n64":
                opts += [("n64-out", "Export as separate save files (Mupen64Plus, RMG)…"),
                         ("pj64-out", "Export for Project64…"),
                         ("n64-in", "Import separate save files…")]
        opts += [("delete", "Delete"), ("details", "Details"), ("cancel", "Cancel")]
        if len(games) > 1:
            opts.remove(("details", "Details"))
        actions = {"backup": self._backup, "restore-one": lambda: self._restore_from(it.game, mine), "rename": lambda: self._rename(it.game), "tidy": self._tidy_selected,
                   "delete": self._delete_games, "details": self.details,
                   "ps1-out": lambda: self._ps1_out(it.game), "ps1-in": lambda: self._ps1_in(it.game),
                   "raw-out": lambda: self._raw_out(it.game), "raw-in": lambda: self._raw_in(it.game),
                   "n64-out": lambda: self._n64_out(it.game, "mupen"), "pj64-out": lambda: self._n64_out(it.game, "pj64"),
                   "n64-in": lambda: self._n64_in(it.game)}
        self.show_overlay(what, "", opts, lambda k: actions.get(k, lambda: None)())

    def main_menu(self):
        scope = self._scope()
        n_orphans = sum(1 for it in scope if it.orphan)
        n_scattered = sum(1 for it in scope if it.scattered)
        where = f" in {short_name(self.cur)}" if self.mode == "games" else ""
        art_on = self.dialog.download_art()
        opts = []
        if self.mode != "systems":
            opts.append(("home", "Back to systems"))
        opts += [("orphans", f"Find saves with no game file{where}… ({n_orphans})"),
                 ("tidy", f"Tidy scattered saves{where}… ({n_scattered})"),
                 ("folders", "Game folders…"),
                 ("where", "Where RetroArch keeps saves"),
                 ("art", f"Download box art from libretro: {'On' if art_on else 'Off'}"),
                 ("refresh", "Refresh"), ("open", "Open this folder"), ("close", "Close manager"), ("cancel", "Cancel")]

        def chosen(key):
            {"home": self.go_home, "orphans": self.find_orphans,
             "tidy": lambda: self.tidy([it.game for it in self._scope()]),
             "folders": self.folders_menu, "where": self.where, "art": self._toggle_art,
             "refresh": self.reload, "open": self._open_folder, "close": self.dialog.destroy}.get(key, lambda: None)()
        self.show_overlay("RetroArch Save Manager", "", opts, chosen)

    def _open_folder(self):
        if self.side == 1:
            p = R.backups_root(self.dialog.backup_dir)
        else:
            it = self.current()
            p = it.game.files[0].path.parent if it is not None and it.kind == "game" and it.game.files else \
                (self.lib.settings.saves_dir if self.lib else None)
        if p is not None:
            Path(p).mkdir(parents=True, exist_ok=True)
            opener = getattr(self.dialog.app, "open_folder", None)
            if opener:
                opener(Path(p))

    def _toggle_art(self):
        on = not self.dialog.download_art()
        self.dialog.set_download_art(on)
        self.loader.download = on
        if on:
            self.loader.asked = {k for k in self.loader.asked if self.art.get(k)}
            for k in [k for k, v in self.art.items() if not v]:
                del self.art[k]
            for side in (0, 1):
                for it in self.items[side]:
                    self._ask_art(it)
        self.toast("Box art downloads " + ("on" if on else "off"))

    def where(self):
        s = self.lib.settings if self.lib else R.Settings()

        def sort(core, content, inside):
            if inside:
                return "next to the game files"
            bits = [b for b, on in (("the game file's folder", content), ("core", core)) if on]
            return "sorted by " + " and ".join(bits) if bits else "all in one folder"
        lines = [f"Settings file: {s.cfg_path or 'not found'}",
                 f"Saves: {s.saves_dir or 'not found'}", f"    {sort(s.sort_saves_core, s.sort_saves_content, s.saves_in_content)}",
                 f"States: {s.states_dir or 'not found'}",
                 f"    {sort(s.sort_states_core, s.sort_states_content, s.states_in_content)}",
                 f"Playlists: {s.playlists_dir or 'not found'}",
                 *([f"Note: {s.other_cfg} uses other folders ({', '.join(str(d) for d in s.other_dirs)}). "
                    "This manager uses the folders the main window shows and leaves those alone."]
                   if s.other_cfg else []),
                 "Game folders: " + (", ".join(str(d) for d in self.lib.roms.dirs) if self.lib and self.lib.roms.dirs
                                     else "none found")]
        self.show_overlay("Where RetroArch keeps saves", "\n".join(lines), [("ok", "Close")], lambda k: None)

    def folders_menu(self):
        mine = list(self.dialog.custom_rom_dirs())
        auto = [d for d in self.dialog.rom_dirs() if str(d) not in mine]
        opts = [("add", "Add a game folder…")]
        opts += [(f"rm{i}", f"Remove {d}") for i, d in enumerate(mine)]
        opts.append(("cancel", "Close"))
        msg = ("Game files are matched to saves to know each game's system and box art, and to find saves "
               "left behind by renamed files.")
        if auto:
            msg += "\n\nFound on their own: " + ", ".join(str(d) for d in auto[:4])

        def chosen(key):
            if key == "add":
                d = self.dialog.ask_directory("Choose a folder with game files (it can hold one folder per system)")
                if d:
                    self.dialog.set_custom_rom_dirs(mine + [str(d)])
                    self.reload()
                    self.toast("Game folder added")
            elif key.startswith("rm"):
                del mine[int(key[2:])]
                self.dialog.set_custom_rom_dirs(mine)
                self.reload()
        self.show_overlay("Game folders", msg, opts, chosen)

    def details(self):
        it = self.current()
        if it is None or it.kind in ("system", "backups"):
            return
        if it.kind == "game":
            g = it.game
            lines = [f"System: {g.system_name}" + (f" · core {g.core}" if g.core else ""),
                     f"Game file: {g.rom.path}" if g.rom else "Game file: not found", ""]
            for f in sorted(g.files, key=lambda f: (f.kind != "sram", f.slot if f.slot is not None else -2, f.name)):
                lines.append(f"{f.name}  ·  {_size(f.size)}  ·  {_when(f.mtime)}")
            places = g.places
            lines += ["", "In " + ("these folders:" if len(places) > 1 else "")] + places
            title = g.title
        else:
            b = it.backup
            lines = [f"System: {b.system_name}", f"Backed up: {b.created:%Y-%m-%d %H:%M}" if b.created else "",
                     f"Folder: {b.path}", ""] + [p.name for p, *_r in b.files]
            title = b.title
        self.show_overlay(title, "\n".join(l for l in lines if l is not None)[:1800], [("ok", "Close")], lambda k: None)

    # ---- backups -----------------------------------------------------------------
    def _backup(self):
        games = self._games()
        done, refused = [], []
        for g in games:
            try:
                R.backup_game(g, self.dialog.backup_dir)
                done.append(g)
            except Exception as ex:  # noqa - OSError, CopyError
                refused.append((g.title, str(ex)))
        self._report(R.Report(done=done, refused=refused), "Backed up", "game")

    def _restore_from(self, g, backups):
        """A game's own backups, newest first: choose one to restore."""
        bs = sorted(backups, key=lambda b: -(b.created.timestamp() if b.created else 0))
        opts = [(str(i), (f"{b.created:%Y-%m-%d %H:%M}" if b.created else b.path.name) +
                 f"  ·  {sum(1 for x in b.files if x[2] == 'state')} states") for i, b in enumerate(bs[:30])]
        opts.append(("cancel", "Cancel"))
        ans = self.prompts.choose(f"Restore {g.title}", "Which backup?", opts)
        if ans.isdigit():
            self._restore([bs[int(ans)]])

    def _restore(self, chosen=None):
        sel = chosen or [it.backup for it in self.selection() if it.kind == "backup"]
        newest = {}
        for b in sel:                                  # several backups of one game: the newest
            if b.key not in newest or (b.created and newest[b.key].created and b.created > newest[b.key].created):
                newest[b.key] = b
        if not newest or not self._close_retroarch():
            return

        def ask(b, existing):
            return self.prompts.choose(
                "Replace the saves in RetroArch?",
                f"“{b.title}” already has {len(existing)} file{'s' if len(existing) != 1 else ''} in RetroArch "
                f"that this backup would replace. The current ones are kept as safety copies.",
                [("replace", "Replace them"), ("skip", "Skip this game"), ("cancel", "Cancel")], "cancel")
        try:
            rep = R.restore_many(self.lib, list(newest.values()), self.dialog.backup_dir, ask)
        except Exception as ex:  # noqa
            self.prompts.showerror("Couldn't finish", str(ex))
            rep = None
        self._report(rep, "Restored")

    def _delete_backups(self):
        sel = [it.backup for it in self.selection() if it.kind == "backup"]
        what = f"{len(sel)} backups" if len(sel) > 1 else f"this backup of “{sel[0].title}”"
        if not sel or not self.prompts.askyesno("Delete", f"Delete {what}?\n\nA safety copy is kept in the "
                                                "backups' _retroarch_safety folder."):
            return
        done, refused = [], []
        for b in sel:
            r = R.delete_backup(b, self.dialog.backup_dir)
            done += r.done
            refused += r.refused
        self._report(R.Report(done=done, refused=refused), "Deleted", "backup")

    def _delete_games(self):
        games = self._games()
        if not games or not self._close_retroarch():
            return
        what = f"{len(games)} games' saves" if len(games) > 1 else f"all saves and states of “{games[0].title}”"
        if not self.prompts.askyesno("Delete", f"Delete {what} from RetroArch?\n\n"
                                     "Safety copies are kept in the backups' _retroarch_safety folder."):
            return
        done, refused = [], []
        for g in games:
            r = R.delete_game(g, self.dialog.backup_dir)
            done += r.done
            refused += r.refused
        self._report(R.Report(done=done, refused=refused), "Deleted")

    # ---- orphans -----------------------------------------------------------------
    def _conflict_rename(self, g, existing):
        return self.prompts.choose(
            "Already there", f"The game file's name is already used by {len(existing)} save file"
            f"{'s' if len(existing) != 1 else ''} (for example {existing[0].name}).",
            [("replace", "Replace them (the old ones are kept as safety copies)"), ("skip", "Skip"),
             ("cancel", "Cancel")], "cancel")

    def _rename(self, g, many=False):
        """Ask which game file this save belongs to; returns 'done' | 'skip' | 'stop'."""
        sug = R.suggest_roms(self.lib, g, 4)
        opts = [(str(i), f"{r.stem}{'  (has saves already)' if taken else ''}") for i, (r, s, taken) in enumerate(sug)]
        opts += [("skip", "Skip this one" if many else "Cancel")]
        if many:
            opts.append(("stop", "Stop"))
        msg = (f"{len(g.files)} file{'s' if len(g.files) != 1 else ''} named “{g.stem}”"
               + (f" ({g.system_name})" if g.system else "") + ".\n")
        msg += ("Rename them to one of these game files?" if sug else "No similar game file was found.")
        ans = self.prompts.choose("Which game is this?", msg, opts, "stop" if many else "skip")
        if not ans.isdigit():
            return ans
        if not self._close_retroarch():
            return "stop"
        rom = sug[int(ans)][0]
        rep = R.rename_game(self.lib, g, rom.stem, self.dialog.backup_dir, self._conflict_rename)
        if rep.refused:
            self.prompts.showinfo("Some files weren't renamed",
                                  "\n".join(f"• {what}: {why}" for what, why in rep.refused[:10]))
        if not many:
            self.reload()
            if rep.done:
                self.toast(f"Renamed to {rom.stem}")
        return "done" if rep.done else "skip"

    def find_orphans(self):
        if self.lib is None:
            return
        if not self.lib.roms.dirs and not len(self.lib.roms):
            if self.prompts.askyesno("No game files found",
                                     "Saves are matched to game files by name, and no game files were found. "
                                     "Add the folder your games are in?"):
                self.folders_menu()
            return
        orphans = [it.game for it in self._scope() if it.orphan]
        if not orphans:
            self.prompts.showinfo("All matched", "Every save here has a game file with the same name.")
            return
        if not self.prompts.askyesno(
                "Saves with no game file", f"{len(orphans)} game{'s' if len(orphans) != 1 else ''} have saves but no "
                "game file with the same name, usually because the file was renamed. RetroArch can't see those "
                "saves. Go through them now?"):
            return
        renamed = 0
        for g in orphans:
            ans = self._rename(g, many=True)
            if ans == "stop":
                break
            renamed += ans == "done"
        self.reload()
        if renamed:
            self.toast(f"Renamed the saves of {renamed} game{'s' if renamed != 1 else ''}")

    # ---- tidying -----------------------------------------------------------------
    def _tidy_selected(self):
        self.tidy(self._games())

    def tidy(self, games):
        if self.lib is None:
            return
        plan = R.tidy_plan(self.lib, games)
        if plan.empty:
            msg = "Every save is where RetroArch looks for it."
            if plan.unknown:
                msg = ("Nothing can be moved safely.\n\n" + "\n".join(
                    f"• {g.title} ({g.system_name}): {why}" for g, why in plan.unknown[:6]))
            self.prompts.showinfo("Tidy scattered saves", msg)
            return
        msg = "\n".join(plan.summary_lines(7))
        msg += ("\n\nOlder duplicates and anything replaced go to the _retroarch_safety folder."
                if plan.dupes else "\n\nNothing is deleted: files are moved to where RetroArch looks now.")
        if plan.unknown:
            msg += f"\n{len(plan.unknown)} game{'s' if len(plan.unknown) != 1 else ''} can't be placed and stay as they are."
        if self.prompts.choose("Tidy scattered saves", msg, [("go", "Move them"), ("cancel", "Cancel")]) != "go":
            return
        if not self._close_retroarch():
            return
        try:
            rep = R.tidy(plan, self.dialog.backup_dir)
        except Exception as ex:  # noqa
            self.prompts.showerror("Couldn't finish", str(ex))
            rep = None
        self._report(rep, "Tidied")

    # ---- standalone emulators -------------------------------------------------------
    def _ps1_out(self, g):
        ds = self.dialog.duckstation_cards()
        if ds is None:
            ds = self.dialog.ask_directory("Choose DuckStation's memcards folder")
            if not ds:
                return
        try:
            f, card = C.ps1_card(g)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Can't copy this card", str(ex))
            return
        if f is None:
            self.prompts.showinfo("No memory card yet", "This game has no memory card (.srm) in RetroArch yet.")
            return
        names = C.duckstation_names(g, card, Path(ds), self.dialog.titles())
        opts = [(str(i), n + ("  (replace it)" if (Path(ds) / n).exists() else "")) for i, n in enumerate(names[:4])]
        opts.append(("cancel", "Cancel"))
        ans = self.prompts.choose(
            "Copy memory card to DuckStation",
            "DuckStation gives each game its own card, named after the game. Choose the name it uses for this "
            "game (a card already there for it is listed first; a replaced card is kept as a safety copy).", opts)
        if not ans.isdigit():
            return
        try:
            dest = C.export_ps1(g, Path(ds), names[int(ans)], self.dialog.backup_dir)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Couldn't copy", str(ex))
            return
        self.toast(f"Copied to DuckStation as {dest.name}")

    def _game_save_path(self, g, suffix=".srm"):
        f = C.main_save(g)
        if f is not None:
            return f.path
        return R.game_location(self.lib, g, "saves") / (g.stem + suffix)

    def _ps1_in(self, g):
        ds = self.dialog.duckstation_cards()
        cards = sorted(Path(ds).glob("*.mcd"), key=lambda p: -p.stat().st_mtime) if ds and Path(ds).is_dir() else []
        if cards:
            opts = [(str(i), f"{p.name}  ·  {_when(p.stat().st_mtime)}") for i, p in enumerate(cards[:40])]
            opts += [("browse", "Another file…"), ("cancel", "Cancel")]
            ans = self.prompts.choose("Use a memory card from DuckStation",
                                      f"Which card should become “{g.title}”'s card in RetroArch?", opts)
            if ans == "browse":
                src = self.dialog.ask_open_file("Choose a PlayStation memory card")
            elif ans.isdigit():
                src = cards[int(ans)]
            else:
                return
        else:
            src = self.dialog.ask_open_file("Choose a PlayStation memory card")
        if not src:
            return
        try:
            dest = self._game_save_path(g)
        except R.RAError as ex:
            self.prompts.showerror("Can't tell where it goes", str(ex))
            return
        if dest.exists() and not self.prompts.askyesno(
                "Replace the card?", f"Replace “{g.title}”'s current card in RetroArch? The current one is "
                "kept as a safety copy."):
            return
        if not self._close_retroarch():
            return
        try:
            C.import_ps1(Path(src), g, dest, self.dialog.backup_dir)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Couldn't use that card", str(ex))
            return
        self.reload()
        self.toast("Memory card copied into RetroArch")

    def _raw_out(self, g):
        start = g.rom.path.parent if g.rom else None
        d = self.dialog.ask_directory("Choose where to put the .sav (standalone emulators often look next to "
                                      "the game file)", start)
        if not d:
            return
        dest = Path(d) / (g.stem + ".sav")
        if dest.exists() and not self.prompts.askyesno(
                "Replace it?", f"{dest.name} is already there. Replace it? The old one is kept as a safety copy."):
            return
        try:
            p = C.export_raw(g, Path(d), self.dialog.backup_dir)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Couldn't export", str(ex))
            return
        self.toast(f"Saved {p.name}")

    def _raw_in(self, g):
        src = self.dialog.ask_open_file("Choose the standalone emulator's save (.sav)")
        if not src:
            return
        if not self.prompts.askyesno("Replace the save?", f"Replace “{g.title}”'s in-game save in RetroArch with "
                                     f"{Path(src).name}? The current one is kept as a safety copy."):
            return
        if not self._close_retroarch():
            return
        try:
            C.import_raw(Path(src), g, self.dialog.backup_dir)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Couldn't import", str(ex))
            return
        self.reload()
        self.toast("Save imported into RetroArch")

    def _n64_out(self, g, flavor):
        d = self.dialog.ask_directory("Choose where to put the save files")
        if not d:
            return
        try:
            files = C.export_n64(g, Path(d), flavor, self.dialog.backup_dir)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Couldn't export", str(ex))
            return
        self.toast("Saved " + ", ".join(p.suffix for p in files))

    def _n64_in(self, g):
        files = self.dialog.ask_open_files("Choose the save files (.eep, .mpk, .sra, .fla)")
        if not files:
            return
        flavor = self.prompts.choose("Which emulator made these?",
                                     "Project64 stores SRAM and FlashRAM saves in a different byte order.",
                                     [("mupen", "Mupen64Plus, RMG or similar"), ("pj64", "Project64"),
                                      ("cancel", "Cancel")])
        if flavor == "cancel":
            return
        try:
            dest = self._game_save_path(g)
        except R.RAError as ex:
            self.prompts.showerror("Can't tell where it goes", str(ex))
            return
        if not self._close_retroarch():
            return
        try:
            C.import_n64([Path(f) for f in files], g, dest, flavor, self.dialog.backup_dir)
        except (OSError, C.ConvertError) as ex:
            self.prompts.showerror("Couldn't import", str(ex))
            return
        self.reload()
        self.toast("Save files combined into RetroArch's save")


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
class RASaveDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("RetroArch Save Manager")
        self.backup_dir = Path(app.backup_dir)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh - 40}+0+0" if sh <= 820 else "1200x740")
        self.minsize(820, 520)
        self.view = RAConsoleView(self, self)
        self.view.pack(fill="both", expand=True)
        self.msg = self.view.prompts
        self.after(10, self.view.start)

    # ---- settings ------------------------------------------------------------------
    @property
    def cfg(self):
        return getattr(self.app, "cfg", None) if isinstance(getattr(self.app, "cfg", None), dict) else {}

    def _save_cfg(self):
        try:
            from emu_save_manager import save_config
            save_config(self.app.cfg)
        except Exception:  # noqa - tests and odd set-ups have no config file
            pass

    def _resolved(self):
        try:
            return self.app.resolve("RetroArch") or {}
        except Exception:  # noqa
            return {}

    def settings(self) -> R.Settings:
        res = self._resolved()
        saves, states = res.get("Saves"), res.get("States")
        extra = [Path(p).parent for p in (saves, states) if p]
        return R.choose_settings(saves, states, extra, self.cfg.get("ra_cfg"))

    def custom_rom_dirs(self):
        return list(self.cfg.get("ra_rom_dirs", []))

    def set_custom_rom_dirs(self, dirs):
        if isinstance(getattr(self.app, "cfg", None), dict):
            self.app.cfg["ra_rom_dirs"] = list(dict.fromkeys(dirs))
            self._save_cfg()

    def rom_dirs(self):
        out = [Path(d) for d in self.custom_rom_dirs()]
        try:
            import cloudsync
            out += [r / "roms" for r in cloudsync.emudeck_roots() if (r / "roms").is_dir()]
        except Exception:  # noqa
            pass
        try:
            lr = L.load_config().get("local_roms")
            if lr:
                out.append(Path(lr))
        except Exception:  # noqa
            pass
        seen, uniq = set(), []
        for d in out:
            k = os.path.normcase(str(d))
            if k not in seen and Path(d).is_dir():
                seen.add(k)
                uniq.append(Path(d))
        return uniq

    def esde_media(self):
        try:
            import libmeta
            return [m for _g, m in libmeta.esde_locations() if m]
        except Exception:  # noqa
            return []

    def download_art(self):
        return bool(self.cfg.get("ra_download_art", True))

    def set_download_art(self, on):
        if isinstance(getattr(self.app, "cfg", None), dict):
            self.app.cfg["ra_download_art"] = bool(on)
            self._save_cfg()

    def duckstation_cards(self):
        try:
            p = self.app.resolve("DuckStation").get("Memory Cards")
        except Exception:  # noqa
            p = None
        return Path(p) if p else None

    def titles(self):
        return getattr(self.app, "titles", None)

    # ---- file choosers (replaced in tests) ----------------------------------------------
    def ask_directory(self, title, initial=None):
        return filedialog.askdirectory(parent=self, title=title, initialdir=str(initial) if initial else None) or None

    def ask_open_file(self, title):
        return filedialog.askopenfilename(parent=self, title=title) or None

    def ask_open_files(self, title):
        return list(filedialog.askopenfilenames(parent=self, title=title) or [])
