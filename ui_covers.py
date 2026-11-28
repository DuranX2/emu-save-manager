"""ROM Library: cover grid, game details panel, and the Artwork window (where game information
comes from, and looking it up)."""
from __future__ import annotations

import queue as pyqueue
import threading
from pathlib import Path
import os
import json
import tkinter as tk
from collections import OrderedDict
from tkinter import messagebox, ttk

import libmeta as M
import romlibrary as L

APP_NAME = "ROM Library"
import apptheme

BADGE = {"here": ("✔", "#2e7d32"), "partial": ("◐", "#e65100"), "local_only": ("●", "#1565c0"),
         "busy": ("⬇", "#6a1b9a"), "elsewhere": ("⇄", "#6d6d6d"), "card_out": ("▣", "#8a7440")}


# Case shapes (height / width of the cover art) until the app has seen enough of a system's covers.
CD_CASE, DVD_CASE, WIDE_BOX = 1.0, 1.4, 0.72
CASE_SHAPES = {
    "psx": CD_CASE, "dreamcast": CD_CASE, "neogeocd": CD_CASE, "pcfx": CD_CASE, "atarijaguarcd": CD_CASE,
    "megacd": CD_CASE, "segacd": CD_CASE, "saturn": CD_CASE, "3do": CD_CASE,
    "snes": WIDE_BOX, "n64": WIDE_BOX,
    "gb": 1.0, "gbc": 1.0, "gba": 1.0, "nds": 0.9, "n3ds": 0.9, "virtualboy": 1.0,
    "psp": 1.7, "psvita": 1.2, "switch": 1.6, "ps3": 1.15, "ps4": 1.2,
}
SHAPE_MIN, SHAPE_MAX, SHAPE_LEARN = 0.6, 1.75, 6


class CoverShapes:
    """The usual cover shape per system: learned from the covers shown (the median, so a few odd
    ones don't matter), else CASE_SHAPES, else a DVD case. Kept in a small file between runs."""

    def __init__(self, path):
        self.path = Path(path)
        self.seen = {}                     # system -> {key: height / width}
        self._dirty = 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.seen = {s: {k: float(v) for k, v in d.items()} for s, d in data.items() if isinstance(d, dict)}
        except (OSError, ValueError, AttributeError):
            pass

    def note(self, system, key, w, h):
        if not w or not h or not system:
            return
        d = self.seen.setdefault(system, {})
        r = round(h / w, 3)
        if d.get(key) != r:
            d[key] = r
            while len(d) > 200:
                d.pop(next(iter(d)))
            self._dirty += 1
            if self._dirty >= 25:
                self.save()

    def shape(self, system) -> float:
        rs = sorted(self.seen.get(system, {}).values())
        if len(rs) >= SHAPE_LEARN:
            n = len(rs)
            r = rs[n // 2] if n % 2 else (rs[n // 2 - 1] + rs[n // 2]) / 2
        else:
            r = CASE_SHAPES.get(system, DVD_CASE)
        return round(min(SHAPE_MAX, max(SHAPE_MIN, r)) * 20) / 20        # steps of 0.05: no jitter

    def for_view(self, systems) -> float:
        """One system: its shape. Several: a DVD case (taller covers are scaled to fit)."""
        systems = set(systems)
        return self.shape(next(iter(systems))) if len(systems) == 1 else DVD_CASE

    def save(self):
        self._dirty = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.seen), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass


class ImageCache:
    """Cover thumbnails as Tk images, made in a background thread and kept for the most recent few hundred."""
    LIMIT = 600

    def __init__(self, app, width, height):
        self.app, self.w, self.h = app, width, height
        self.images = OrderedDict()        # (key, kind, w, h) -> PhotoImage | None
        self.pending = set()
        self.q = pyqueue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def get(self, key, kind="cover", w=None, h=None):
        w, h = w or self.w, h or self.h
        k = (key, kind, w, h)
        if k in self.images:
            self.images.move_to_end(k)
            return self.images[k]
        store = self.app.meta
        if store and kind in store.get(key).get("media", {}) and k not in self.pending:
            self.pending.add(k)
            self.q.put(k)
        return None

    def forget_missing(self):
        """Ask again for the covers that had no picture (a look-up may have found one since)."""
        for k in [k for k, v in self.images.items() if v is None]:
            self.images.pop(k, None)

    def forget(self, key=None):
        for k in [k for k in self.images if key is None or k[0] == key]:
            self.images.pop(k, None)
        self.pending = {k for k in self.pending if key is not None and k[0] != key}

    def _worker(self):
        while True:
            batch = [self.q.get()]
            try:
                while len(batch) < 40:
                    batch.append(self.q.get_nowait())
            except pyqueue.Empty:
                pass
            store = self.app.meta
            if not store:
                continue
            missing = [k for k in batch if not store.media_path(k[0], k[1])]
            if missing:                                   # copy them from the library in one go
                try:
                    store.fetch_media({k[0] for k in missing}, tuple({k[1] for k in missing}))
                except Exception:  # noqa
                    pass
            for k in batch:
                data = None
                th = M.thumbnail(store.media_path(k[0], k[1]), k[2], k[3])
                if th:
                    try:
                        data = th.read_bytes()
                    except OSError:
                        data = None
                self.app.events.put(("call", lambda k=k, data=data: self._made(k, data)))

    def _made(self, k, data):
        self.pending.discard(k)
        img = None
        if data:
            try:
                img = tk.PhotoImage(master=self.app, data=data)
            except tk.TclError:
                img = None
        self.images[k] = img
        shapes = getattr(self.app, "cover_shapes", None)
        if img is not None and k[1] == "cover" and shapes is not None and self.app.lib:
            it = self.app.lib.get(k[0])
            if it:
                shapes.note(it.system, k[0], img.width(), img.height())
        while len(self.images) > self.LIMIT:
            self.images.popitem(last=False)
        self.app.on_image(k[0])


class CoverGrid(ttk.Frame):
    """A scrolling grid of box art.  Only the rows on screen are drawn, so thousands of games are fine."""
    GAP = 14

    def __init__(self, master, app, tile_w=150):
        super().__init__(master)
        self.app = app
        self.tw = tile_w
        self.shape = DVD_CASE
        self.cover_h = int(tile_w * self.shape)
        self.th = self.cover_h + 44
        self.keys = []
        self.sel = []
        self.anchor = None
        self.cols = 1
        self.canvas = tk.Canvas(self, highlightthickness=0, background=apptheme.COVER_BG)
        sbstyle = "Lib.Vertical.TScrollbar" if app.compact else "Vertical.TScrollbar"
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self._yview, style=sbstyle)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        c = self.canvas
        c.bind("<Configure>", lambda e: self.layout())
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Double-1>", lambda e: self.app.on_grid_double())
        for seq, step in (("<Button-4>", -1), ("<Button-5>", 1)):
            c.bind(seq, lambda e, s=step: self._scroll_units(s))
        c.bind("<MouseWheel>", lambda e: self._scroll_units(-1 if e.delta > 0 else 1))
        for key, d in (("<Left>", -1), ("<Right>", 1), ("<Up>", "up"), ("<Down>", "down")):
            c.bind(key, lambda e, d=d: self._move(d, e))
        c.bind("<Return>", lambda e: self.app.on_grid_double())
        c.bind("<Control-a>", lambda e: self.set_selection(list(self.keys)))
        import mainpad
        mainpad.register_scroll(c, self._scroll_units)          # the right stick, like the wheel
        self._d = None

    # -- data ---------------------------------------------------------------
    def set_keys(self, keys):
        self.keys = list(keys)
        keep = set(self.keys)
        self.sel = [k for k in self.sel if k in keep]
        self.layout()

    def set_selection(self, keys, notify=True):
        self.sel = [k for k in keys if k in set(self.keys)]
        self.anchor = self.sel[-1] if self.sel else None
        self.draw()
        if notify:
            self.app.on_grid_select()

    def see(self, key):
        if key not in self.keys:
            return
        i = self.keys.index(key)
        row = i // self.cols
        top, bottom = row * (self.th + self.GAP), (row + 1) * (self.th + self.GAP)
        h = self.canvas.winfo_height()
        total = self._total_h()
        y0 = self.canvas.canvasy(0)
        if top < y0:
            self.canvas.yview_moveto(top / total)
        elif bottom > y0 + h:
            self.canvas.yview_moveto(max(0, bottom - h) / total)
        self.draw()

    def set_shape(self, shape):
        """Tiles shaped like the covers shown (height / width): CD cases square, DVD cases tall."""
        if abs(shape - self.shape) < 0.01:
            return
        self.shape = shape
        self.cover_h = int(round(self.tw * shape))
        self.th = self.cover_h + 44
        self.layout()

    # -- geometry -------------------------------------------------------------
    def _total_h(self):
        rows = (len(self.keys) + self.cols - 1) // self.cols
        return max(1, rows * (self.th + self.GAP) + self.GAP)

    def layout(self):
        w = max(1, self.canvas.winfo_width())
        self.cols = max(1, (w - self.GAP) // (self.tw + self.GAP))
        self.canvas.configure(scrollregion=(0, 0, w, self._total_h()))
        self.draw()

    def _origin(self):
        w = max(1, self.canvas.winfo_width())
        used = self.cols * self.tw + (self.cols - 1) * self.GAP
        return max(self.GAP, (w - used) // 2)

    def _tile_at(self, x, y):
        cx, cy = self.canvas.canvasx(x), self.canvas.canvasy(y)
        x0 = self._origin()
        col = int((cx - x0) // (self.tw + self.GAP))
        row = int((cy - self.GAP) // (self.th + self.GAP))
        if col < 0 or col >= self.cols or row < 0:
            return None
        if (cx - x0) - col * (self.tw + self.GAP) > self.tw or (cy - self.GAP) - row * (self.th + self.GAP) > self.th:
            return None
        i = row * self.cols + col
        return self.keys[i] if i < len(self.keys) else None

    def _yview(self, *a):
        self.canvas.yview(*a)
        self.draw()

    def _scroll_units(self, n):
        self.canvas.yview_scroll(n * 3, "units")
        self.draw()
        return "break"

    # -- drawing --------------------------------------------------------------
    def draw(self):
        c = self.canvas
        c.delete("all")
        if not self.keys:
            c.create_text(c.winfo_width() // 2, 60, text="No games to show", fill="#b0b6c0",
                          font=("TkDefaultFont", 12))
            return
        h = c.winfo_height()
        y0 = c.canvasy(0)
        first = max(0, int((y0 - self.GAP) // (self.th + self.GAP)))
        last = int((y0 + h) // (self.th + self.GAP)) + 1
        x0 = self._origin()
        sel = set(self.sel)
        for row in range(first, last + 1):
            for col in range(self.cols):
                i = row * self.cols + col
                if i >= len(self.keys):
                    return
                self._tile(self.keys[i], x0 + col * (self.tw + self.GAP), self.GAP + row * (self.th + self.GAP),
                           self.keys[i] in sel)

    def _tile(self, key, x, y, selected):
        c, app = self.canvas, self.app
        lib = app.lib
        it = lib.get(key)
        if not it:
            return
        if selected:
            c.create_rectangle(x - 6, y - 6, x + self.tw + 6, y + self.th + 2, fill=apptheme.ACCENT_DARK, outline=apptheme.ACCENT, width=2)
        img = app.images.get(key, "cover", self.tw, self.cover_h)
        top = y
        if img:
            # every cover stands on the same line (the bottom of the cover space), centred
            ih = min(img.height(), self.cover_h)
            top = y + self.cover_h - ih
            c.create_image(x + self.tw // 2, y + self.cover_h, image=img, anchor="s")
        else:
            c.create_rectangle(x, y, x + self.tw, y + self.cover_h, fill="#1f3a37", outline=apptheme.LINE)
            c.create_text(x + self.tw // 2, y + 22, text=L.system_name(it.system), fill=apptheme.DIM,
                          font=("TkDefaultFont", 8), width=self.tw - 10)
            c.create_text(x + self.tw // 2, y + self.cover_h // 2, text=app.display_title(key), fill="#e8ecf2",
                          font=("TkDefaultFont", 10, "bold"), width=self.tw - 16, justify="center")
        st = lib.status(key)[0]
        badge = None
        if app.active_job(key):
            badge = BADGE["busy"]
        elif st in BADGE:
            badge = BADGE[st]
        elif lib.elsewhere(key):
            badge = BADGE["elsewhere"]
        if badge:
            bx, by = x + self.tw - 16, top + 14                  # on the cover's corner
            c.create_oval(bx - 13, by - 13, bx + 13, by + 13, fill=badge[1], outline="white", width=2)
            c.create_text(bx, by, text=badge[0], fill="white", font=("TkDefaultFont", 11, "bold"))
        if lib.is_favourite(key):
            c.create_text(x + 14, top + 14, text="★", fill="#ffd54f", font=("TkDefaultFont", 15, "bold"))
        job = app.active_job(key)
        if job and job.state == "running" and job.kind in ("checkout", "add"):
            pw = int((self.tw - 8) * job.percent / 100)
            c.create_rectangle(x + 4, y + self.cover_h - 10, x + self.tw - 4, y + self.cover_h - 4, fill="#111", outline="")
            c.create_rectangle(x + 4, y + self.cover_h - 10, x + 4 + pw, y + self.cover_h - 4, fill="#b388ff", outline="")
        title = app.display_title(key)
        t = c.create_text(x + self.tw // 2, y + self.cover_h + 6, text=title, fill="white" if selected else "#e8ecf2",
                          font=("TkDefaultFont", 9), width=self.tw, anchor="n", justify="center")
        bb = c.bbox(t)
        if bb and bb[3] - bb[1] > 34:                 # keep to two lines
            c.delete(t)
            short = title
            while len(short) > 3:
                short = short[:-2]
                t = c.create_text(x + self.tw // 2, y + self.cover_h + 6, text=short.rstrip() + "…",
                                  fill="white" if selected else "#e8ecf2", font=("TkDefaultFont", 9),
                                  width=self.tw, anchor="n", justify="center")
                bb = c.bbox(t)
                if bb and bb[3] - bb[1] <= 34:
                    break
                c.delete(t)

    # -- input ------------------------------------------------------------------
    def _press(self, e):
        self.canvas.focus_set()
        self._d = {"y": e.y, "moved": False, "state": e.state}

    def _drag(self, e):
        d = self._d
        if not d:
            return
        dy = e.y - d["y"]
        if abs(dy) >= 8:
            total = self._total_h()
            self.canvas.yview_moveto(max(0.0, (self.canvas.canvasy(0) - dy) / total))
            d["y"] = e.y
            d["moved"] = True
            self.draw()

    def _release(self, e):
        d, self._d = self._d, None
        if not d or d["moved"]:
            return
        key = self._tile_at(e.x, e.y)
        ctrl, shift = bool(d["state"] & 0x4), bool(d["state"] & 0x1)
        if key is None:
            if not ctrl and not shift:
                self.set_selection([])
            return
        if ctrl:
            sel = [k for k in self.sel if k != key] if key in self.sel else self.sel + [key]
            self.sel = sel
            self.anchor = key
        elif shift and self.anchor in self.keys:
            a, b = sorted((self.keys.index(self.anchor), self.keys.index(key)))
            self.sel = self.keys[a:b + 1]
        else:
            self.sel = [key]
            self.anchor = key
        self.draw()
        self.app.on_grid_select()

    def _move(self, d, e):
        if not self.keys:
            return "break"
        cur = self.keys.index(self.anchor) if self.anchor in self.keys else -1
        step = {"up": -self.cols, "down": self.cols}.get(d, d)
        nxt = min(len(self.keys) - 1, max(0, cur + step)) if cur >= 0 else 0
        self.set_selection([self.keys[nxt]])
        self.see(self.keys[nxt])
        return "break"

    @property
    def dragged(self):
        return bool(self._d and self._d["moved"])


class DetailsPanel(ttk.Frame):
    """Cover, facts, description and screenshot for the selected game, with its actions."""

    def __init__(self, master, app, width=330):
        super().__init__(master, padding=(10, 0, 4, 0))
        self.app = app
        self.pw = width
        self.cover_w, self.cover_h = width - 40, int((width - 40) * 1.4)
        self.key = None
        self.canvas = tk.Canvas(self, highlightthickness=0, width=width, background=apptheme.BG)
        sbstyle = "Lib.Vertical.TScrollbar" if app.compact else "Vertical.TScrollbar"
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style=sbstyle)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        # The wheel scrolls the pane wherever the mouse is on it: every widget inside gets this tag
        self._wheel_tag = f"DetailsWheel{id(self)}"
        self.bind_class(self._wheel_tag, "<Button-4>", lambda e: self._wheel(-2))
        self.bind_class(self._wheel_tag, "<Button-5>", lambda e: self._wheel(2))
        self.bind_class(self._wheel_tag, "<MouseWheel>", lambda e: self._wheel(-2 if e.delta > 0 else 2))
        self._tag_wheel(self.canvas)
        self._tag_wheel(self.inner)
        self.show(None)

    def _wheel(self, step):
        if self.canvas.yview() != (0.0, 1.0):          # only when there's something to scroll
            self.canvas.yview_scroll(step, "units")
        return "break"

    def _tag_wheel(self, w):
        tags = w.bindtags()
        if self._wheel_tag not in tags:
            w.bindtags((self._wheel_tag,) + tags)
        for c in w.winfo_children():
            self._tag_wheel(c)

    def _clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)

    def _label(self, text, **kw):
        lbl = ttk.Label(self.inner, text=text, wraplength=self.pw - 30, **kw)
        lbl.pack(anchor="w", fill="x")
        return lbl

    def show(self, keys):
        try:
            self._show(keys)
        finally:
            self._tag_wheel(self.inner)              # the new labels and pictures scroll the pane too

    def _show(self, keys):
        self._clear()
        app = self.app
        keys = keys or []
        self.key = keys[0] if len(keys) == 1 else None
        if not keys:
            self._label("Select a game to see its details.", foreground=apptheme.DIM).pack(pady=30)
            return
        lib = app.lib
        if len(keys) > 1:
            size = sum(lib.get(k).size for k in keys)
            self._label(f"{len(keys)} games selected", font=("TkDefaultFont", 12, "bold")).pack(pady=(10, 2))
            self._label(L.human(size))
            self._buttons(keys)
            return
        key = keys[0]
        it = lib.get(key)
        rec = app.meta.get(key) if app.meta else {}
        img = app.images.get(key, "cover", self.cover_w, self.cover_h)
        if img:
            tk.Label(self.inner, image=img, borderwidth=0).pack(pady=(6, 8))
        else:
            ph = tk.Canvas(self.inner, width=self.cover_w, height=int(self.cover_w * 0.6), highlightthickness=0,
                           background="#1f3a37")
            ph.create_text(self.cover_w // 2, int(self.cover_w * 0.3), text="No cover yet" if not rec.get("media")
                           else "Loading…", fill="#c0c6d0")
            ph.pack(pady=(6, 8))
        self._label(rec.get("title") or it.title, font=("TkDefaultFont", 13, "bold"))
        if rec.get("title") and rec["title"] != it.title:
            self._label(it.title, foreground=apptheme.DIM)
        facts = [("System", L.system_name(it.system)), ("Released", rec.get("date") or rec.get("year")),
                 ("Genre", rec.get("genre")), ("Players", rec.get("players")),
                 ("Developer", rec.get("developer")), ("Publisher", rec.get("publisher")),
                 ("Rating", f"{rec['rating']} / 100" if rec.get("rating") else ""), ("Size", L.human(it.size))]
        grid = ttk.Frame(self.inner)
        grid.pack(anchor="w", fill="x", pady=(6, 4))
        r = 0
        for a, b in facts:
            if b:
                ttk.Label(grid, text=a, foreground=apptheme.DIM).grid(row=r, column=0, sticky="nw", padx=(0, 10))
                ttk.Label(grid, text=b, wraplength=self.pw - 130).grid(row=r, column=1, sticky="w")
                r += 1
        st, detail = lib.status(key)
        where = {"here": "✔ On this machine", "partial": f"◐ Incomplete on this machine ({detail})",
                 "local_only": "Only on this machine (not in the library)", "not_here": "In the library",
                 "card_out": f"Checked out to an SD card that isn't inserted ({detail})"}[st]
        other = lib.elsewhere(key)
        if other:
            where += " · on " + ", ".join(other)
        job = app.active_job(key)
        if job:
            where = (f"{L.KINDS_VERB.get(job.kind, job.kind)} {job.percent}%" if job.state == "running"
                     else "Waiting in the transfer queue")
        self._label(where, foreground=apptheme.STATUS.get(st, apptheme.DIM))
        self._buttons([key])
        if rec.get("desc"):
            ttk.Separator(self.inner).pack(fill="x", pady=8)
            self._label(rec["desc"])
        shot = app.images.get(key, "screenshot", self.cover_w, int(self.cover_w * 0.8))
        if shot:
            tk.Label(self.inner, image=shot, borderwidth=0).pack(pady=(10, 4))
        src = rec.get("source")
        if src:
            names = {"es-de": "ES-DE", "screenscraper": "ScreenScraper", "libretro": "libretro thumbnails",
                     "manual": "ScreenScraper (picked by hand)"}
            self._label(f"Information from {names.get(src, src)}", foreground=apptheme.DIM, font=("TkDefaultFont", 8)).pack(pady=(8, 0))
        row = ttk.Frame(self.inner)
        row.pack(anchor="w", pady=(6, 10))
        if app.can_match():
            ttk.Button(row, text="Wrong game? Find the right one…",
                       command=lambda: MatchDialog(app, key)).pack(side="left", padx=(0, 6))
        if key in app.lib.items and getattr(app, "meta", None) is not None:
            ttk.Button(row, text="Choose cover…", command=lambda: app.choose_picture(key, "cover")).pack(side="left")
            ttk.Button(row, text="Edit…", command=lambda: app.edit_info(key)).pack(side="left", padx=(6, 0))

    def _buttons(self, keys):
        app, lib = self.app, self.app.lib
        sts = {lib.status(k)[0] for k in keys}
        row = ttk.Frame(self.inner)
        row.pack(anchor="w", fill="x", pady=(8, 2))
        if sts & {"not_here", "partial"}:
            ttk.Button(row, text="⬇ Check out", command=app.check_out).pack(side="left", padx=(0, 6))
        if sts & {"here", "partial"}:
            ttk.Button(row, text="⬆ Check in", command=app.check_in).pack(side="left", padx=(0, 6))
        if "local_only" in sts:
            ttk.Button(row, text="Add to library", command=app.add_to_library).pack(side="left", padx=(0, 6))
        if any(k in lib.items for k in keys):
            fav = all(lib.is_favourite(k) for k in keys)
            ttk.Button(row, text="☆ Unfavourite" if fav else "★ Favourite",
                       command=app.toggle_favourite).pack(side="left")


L.KINDS_VERB = {"checkout": "Copying", "add": "Adding to library", "checkin": "Checking in", "remove": "Removing"}


class ArtworkDialog(tk.Toplevel):
    """Where game information comes from, and looking up what's missing."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Artwork and game information")
        self.transient(app)
        self.resizable(True, False)
        creds = M.load_ss_creds()
        self.v_user = tk.StringVar(value=creds.get("user", ""))
        self.v_pass = tk.StringVar(value=creds.get("password", ""))
        self.v_devid = tk.StringVar(value=creds.get("devid", ""))
        self.v_devpass = tk.StringVar(value=creds.get("devpassword", ""))
        cfg = app.cfg.setdefault("artwork", {})
        self.v_esde = tk.BooleanVar(value=cfg.get("esde", True))
        self.v_ss = tk.BooleanVar(value=cfg.get("screenscraper", True))
        self.v_lr = tk.BooleanVar(value=cfg.get("libretro", True))
        self.v_scope = tk.StringVar(value="all")
        self.v_refresh = tk.BooleanVar(value=False)

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Where game information comes from (tried in this order)",
                  font=("TkDefaultFont", 11, "bold")).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        es = app.esde_source()
        ttk.Checkbutton(f, text="ES-DE on this machine", variable=self.v_esde).grid(row=r, column=0, sticky="w", pady=(6, 0))
        ttk.Label(f, text=(es.summary() + ". Run this on the machine where you scraped in ES-DE.") if es.available
                  else "Nothing found on this machine (run this on the machine where you scraped in ES-DE).",
                  foreground=apptheme.DIM, wraplength=520).grid(row=r, column=1, columnspan=2, sticky="w", pady=(6, 0)); r += 1
        ttk.Checkbutton(f, text="ScreenScraper", variable=self.v_ss).grid(row=r, column=0, sticky="nw", pady=(8, 0))
        box = ttk.Frame(f)
        box.grid(row=r, column=1, columnspan=2, sticky="w", pady=(8, 0)); r += 1
        for i, (label, var, secret) in enumerate((("Your username:", self.v_user, False), ("Your password:", self.v_pass, True),
                                                  ("Developer ID:", self.v_devid, False),
                                                  ("Developer password:", self.v_devpass, True))):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", pady=1)
            ttk.Entry(box, textvariable=var, width=28, show="•" if secret else "").grid(row=i, column=1, sticky="w", padx=6)
        ttk.Label(box, text="The developer ID comes from ScreenScraper's forum. These are kept only on this "
                  "machine, never in the library.", foreground=apptheme.DIM, wraplength=420).grid(row=4, column=0, columnspan=2, sticky="w")
        tb = ttk.Frame(box)
        tb.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(tb, text="Test", command=self.test).pack(side="left")
        self.test_label = ttk.Label(tb, text="", foreground=apptheme.DIM, wraplength=360)
        self.test_label.pack(side="left", padx=8)
        ttk.Checkbutton(f, text="libretro thumbnails", variable=self.v_lr).grid(row=r, column=0, sticky="w", pady=(8, 0))
        ttk.Label(f, text="Free box art and screenshots, no account needed (no descriptions).",
                  foreground=apptheme.DIM).grid(row=r, column=1, columnspan=2, sticky="w", pady=(8, 0)); r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=10); r += 1
        ttk.Label(f, text="Look up games without a cover or description:").grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        sc = ttk.Frame(f)
        sc.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        sys_name = L.system_name(app.system_sel) if app.system_sel != "__all__" else None
        ttk.Radiobutton(sc, text="All games", variable=self.v_scope, value="all").pack(side="left")
        if sys_name:
            ttk.Radiobutton(sc, text=f"{sys_name} only", variable=self.v_scope, value="system").pack(side="left", padx=10)
        if app.selected():
            ttk.Radiobutton(sc, text=f"The {len(app.selected())} selected", variable=self.v_scope,
                            value="selected").pack(side="left", padx=10)
        use = ttk.Frame(f)
        use.grid(row=r, column=0, columnspan=3, sticky="we", pady=(4, 0)); r += 1
        ttk.Label(use, text="Space used:").pack(side="left")
        self.usage_label = ttk.Label(use, text="working it out…", foreground=apptheme.DIM, wraplength=430)
        self.usage_label.pack(side="left", padx=6)
        ttk.Button(use, text="Open folder", command=lambda: app.open_folder(M.MEDIA_CACHE.parent)).pack(side="right")
        self._show_usage()
        skip = ttk.Frame(f)
        skip.grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 0)); r += 1
        ttk.Label(skip, text="Left out of \"All games\":").pack(side="left")
        self.skip_label = ttk.Label(skip, text="", foreground=apptheme.DIM, wraplength=360)
        self.skip_label.pack(side="left", padx=6)
        ttk.Button(skip, text="Choose systems…", command=self.choose_skip).pack(side="left")
        self._show_skip()
        ttk.Checkbutton(f, text="Look up again even if a game already has information", variable=self.v_refresh).grid(
            row=r, column=0, columnspan=3, sticky="w"); r += 1
        self.progress = ttk.Progressbar(f, maximum=100)
        self.progress.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(10, 2)); r += 1
        self.status = ttk.Label(f, text=app.scrape_status or "", foreground=apptheme.DIM, wraplength=560)
        self.status.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Close", command=self.close).pack(side="right")
        ttk.Button(btns, text="Open log", command=app.open_log).pack(side="right", padx=(0, 6))
        self.stop_btn = ttk.Button(btns, text="Stop", command=app.stop_scrape)
        self.stop_btn.pack(side="right", padx=(0, 8))
        self.start_btn = ttk.Button(btns, text="Start", command=self.start)
        self.start_btn.pack(side="right", padx=(0, 8))
        self.sync_buttons()
        app.artwork_dialog = self

    def _creds(self):
        return {"user": self.v_user.get().strip(), "password": self.v_pass.get().strip(),
                "devid": self.v_devid.get().strip(), "devpassword": self.v_devpass.get().strip()}

    def _save(self):
        M.save_ss_creds(self._creds())
        self.app.cfg.setdefault("artwork", {}).update(
            {"esde": self.v_esde.get(), "screenscraper": self.v_ss.get(), "libretro": self.v_lr.get()})
        L.save_config(self.app.cfg)

    def _show_usage(self):
        """This machine's share at once; the library's from the share in the background."""
        app = self.app
        h = L.human

        def text(loc, lib):
            t = (f"this machine {h(loc['total'][0])} (pictures {h(loc['pictures'][0])}, "
                 f"thumbnails {h(loc['thumbnails'][0])}")
            if loc["other libraries"][0]:
                t += f", other libraries {h(loc['other libraries'][0])}"
            t += ")"
            if lib == "…":
                return t + " · the library: checking…"
            if lib is None:
                return t + " · the library: couldn't check"
            return t + f" · the library: {h(lib[0])} in {lib[1]:,} file{'' if lib[1] == 1 else 's'}"

        loc = M.local_usage(app.meta)
        self.usage_label.config(text=text(loc, "…"))

        def work():
            lib = M.library_usage(app.lib) if app.lib else None

            def done():
                try:
                    self.usage_label.config(text=text(loc, lib))
                except tk.TclError:
                    pass                                  # the window was closed meanwhile
            app.events.put(("call", done))
        threading.Thread(target=work, daemon=True).start()

    def _show_skip(self):
        skip = sorted(self.app.scrape_skip(), key=L.system_name)
        text = ", ".join(L.system_name(x) for x in skip[:6]) + (f" and {len(skip) - 6} more" if len(skip) > 6 else "")
        self.skip_label.config(text=text or "nothing")

    def choose_skip(self):
        systems = sorted({it.system for it in self.app.lib.items.values()}, key=L.system_name)
        d = SystemPicker(self, systems, self.app.scrape_skip(), title="Leave out of artwork look-ups",
                         text="These systems are skipped when looking up all games. You can still look one "
                              "up on its own (right-click it, or LT on a controller).")
        self.wait_window(d)
        if d.result is not None:
            self.app.set_scrape_skip(d.result)
            self._show_skip()

    def test(self):
        creds = self._creds()
        self.test_label.config(text="Testing…", foreground=apptheme.DIM)

        def work():
            src = M.ScreenScraperSource(creds)
            if not src.available:
                msg, color = "Enter all four: your username and password, and the developer ID and password.", apptheme.STATUS["partial"]
            else:
                try:
                    msg, color = src.check_login(), apptheme.STATUS["here"]
                except M.SourceUnavailable as ex:
                    msg, color = str(ex), apptheme.STATUS["failed"]
            self.app.events.put(("call", lambda: self.winfo_exists() and self.test_label.config(text=msg, foreground=color)))
        threading.Thread(target=work, daemon=True).start()

    def start(self):
        self._save()
        app = self.app
        scope = self.v_scope.get()
        if scope == "selected":
            keys = app.selected()
        elif scope == "system":
            keys = [k for k in app.lib.items if app.lib.items[k].system == app.system_sel]
        else:
            skip = app.scrape_skip()
            keys = [k for k, it in app.lib.items.items() if it.system not in skip]
        app.start_scrape(keys, esde=self.v_esde.get(), ss=self.v_ss.get(), lr=self.v_lr.get(),
                         refresh=self.v_refresh.get())
        self.sync_buttons()

    def sync_buttons(self):
        running = self.app.scraping()
        self.start_btn.state(["disabled"] if running else ["!disabled"])
        self.stop_btn.state(["!disabled"] if running else ["disabled"])

    def update_progress(self, done, total, msg):
        if total:
            self.progress["value"] = 100 * done / total
        self.status.config(text=msg)
        self.sync_buttons()

    def close(self):
        self._save()
        self.app.artwork_dialog = None
        self.destroy()


class EditInfoDialog(tk.Toplevel):
    """Edit a game's information by hand: title, release, genre, players, developer, publisher,
    description. Saved to the library for every machine; edited fields are never replaced by a
    look-up. Empty a field to remove it."""
    FIELDS = (("title", "Title"), ("released", "Released"), ("genre", "Genre"), ("players", "Players"),
              ("developer", "Developer"), ("publisher", "Publisher"))

    def __init__(self, app, key):
        super().__init__(app)
        self.app, self.key = app, key
        it = app.lib.get(key)
        rec = app.meta.get(key)
        self.date_field = "date" if rec.get("date") else "year"
        self.title(f"Edit information: {app.display_title(key)}")
        self.transient(app)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{L.system_name(it.system)} · {it.path}", foreground=apptheme.DIM,
                  wraplength=460).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.vars = {}
        for r, (name, label) in enumerate(self.FIELDS, 1):
            field = self.date_field if name == "released" else name
            v = tk.StringVar(value=str(rec.get(field) or (it.title if name == "title" else "")))
            self.vars[name] = v
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            e = ttk.Entry(f, textvariable=v, width=48)
            e.grid(row=r, column=1, sticky="we", pady=2)
            if r == 1:
                e.focus_set()
                e.select_range(0, "end")
        r = len(self.FIELDS) + 1
        ttk.Label(f, text="Description").grid(row=r, column=0, sticky="nw", padx=(0, 8), pady=2)
        self.desc = tk.Text(f, width=48, height=8, wrap="word", relief="flat", borderwidth=4, font="TkDefaultFont")
        c = apptheme.colors(app)
        self.desc.configure(background=c["log"], foreground=c["log_fg"], insertbackground=c["log_fg"])
        self.desc.insert("1.0", rec.get("desc", ""))
        self.desc.grid(row=r, column=1, sticky="nsew", pady=2)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(r, weight=1)
        ttk.Label(f, text="Saved to the library for every machine. Look-ups won't replace what you edit; "
                          "empty a field to remove it. The game's file isn't renamed.",
                  foreground=apptheme.DIM, wraplength=460).grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        b = ttk.Frame(f)
        b.grid(row=r + 2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(b, text="Save", command=self.save).pack(side="left", padx=(0, 6))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())

    def fields(self) -> dict:
        out = {}
        for name, _label in self.FIELDS:
            out[self.date_field if name == "released" else name] = self.vars[name].get()
        out["desc"] = self.desc.get("1.0", "end").strip()
        return out

    def save(self):
        fields = self.fields()
        it = self.app.lib.get(self.key)
        rec = self.app.meta.get(self.key)
        if fields["title"].strip() == it.title and not rec.get("title"):
            fields.pop("title")                       # unchanged file-name title: nothing to store
        self.destroy()
        self.app.save_info(self.key, fields)


class SystemPicker(tk.Toplevel):
    """Tick systems in a list (click, Space, or A on a controller toggles one); result: the set, or None."""

    def __init__(self, master, systems, chosen, title="Systems", text=""):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.systems = list(systems)
        self.result = None
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        if text:
            ttk.Label(f, text=text, wraplength=380, foreground=apptheme.DIM).pack(anchor="w", pady=(0, 6))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        self.lb = tk.Listbox(box, selectmode="multiple", height=min(16, max(4, len(systems))), width=40,
                             exportselection=False, activestyle="dotbox")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        for i, s in enumerate(self.systems):
            self.lb.insert("end", L.system_name(s))
            if s in chosen:
                self.lb.selection_set(i)
        self.lb.bind("<Return>", self._toggle)                 # A on a controller
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="OK", command=self.ok).pack(side="right", padx=(0, 6))
        ttk.Button(b, text="None", command=lambda: self.lb.selection_clear(0, "end")).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        self.lb.focus_set()
        if self.systems:
            self.lb.activate(0)

    def _toggle(self, _e=None):
        i = self.lb.index("active")
        if self.lb.selection_includes(i):
            self.lb.selection_clear(i)
        else:
            self.lb.selection_set(i)
        return "break"

    def ok(self):
        self.result = {self.systems[i] for i in self.lb.curselection()}
        self.destroy()


class MatchDialog(tk.Toplevel):
    """Search ScreenScraper by name and pick the right game for one library entry."""

    def __init__(self, app, key):
        super().__init__(app)
        self.app, self.key = app, key
        it = app.lib.get(key)
        self.title("Find the right game")
        self.transient(app)
        self.geometry("560x380")
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{it.title}  ({L.system_name(it.system)})", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=6)
        import re
        self.v_q = tk.StringVar(value=re.sub(r"\s*[\(\[].*", "", it.title))
        e = ttk.Entry(row, textvariable=self.v_q)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.search())
        ttk.Button(row, text="Search", command=self.search).pack(side="left", padx=(6, 0))
        self.tv = ttk.Treeview(f, columns=("title", "year"), show="headings", height=10)
        self.tv.heading("title", text="Game")
        self.tv.heading("year", text="Year")
        self.tv.column("title", width=420)
        self.tv.column("year", width=70)
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<Double-1>", lambda ev: self.use())
        self.msg = ttk.Label(f, text="", foreground=apptheme.DIM)
        self.msg.pack(anchor="w", pady=4)
        b = ttk.Frame(f)
        b.pack(fill="x")
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(b, text="Use this game", command=self.use).pack(side="right", padx=(0, 6))
        self.search()

    def search(self):
        q, system = self.v_q.get().strip(), self.app.lib.get(self.key).system
        self.msg.config(text="Searching…")

        def work():
            try:
                res, err = M.ScreenScraperSource(M.load_ss_creds()).search(system, q), ""
            except (M.SourceUnavailable, M.QuotaReached) as ex:
                res, err = [], str(ex)
            self.app.events.put(("call", lambda: self.winfo_exists() and self._show(res, err)))
        threading.Thread(target=work, daemon=True).start()

    def _show(self, res, err):
        self.tv.delete(*self.tv.get_children())
        for gid, title, year in res:
            self.tv.insert("", "end", iid=str(gid), values=(title, year))
        self.msg.config(text=err or (f"{len(res)} result(s). Double-click one to use it." if res else "Nothing found."))

    def use(self):
        sel = self.tv.selection()
        if not sel:
            return
        gid, key, app = sel[0], self.key, self.app
        self.msg.config(text="Getting the game's information…")

        def work():
            try:
                res = M.ScreenScraperSource(M.load_ss_creds()).by_id(gid)
                if res:
                    rec, pics = res
                    rec["source"] = "manual"
                    app.meta.put(key, rec, pics, replace=True)     # a hand-picked match replaces what was there
                    app.meta.flush()
                err = "" if res else "ScreenScraper didn't return that game."
            except (M.SourceUnavailable, M.QuotaReached) as ex:
                err = str(ex)
            app.events.put(("call", lambda: self._done(err)))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, err):
        if err:
            if self.winfo_exists():
                self.msg.config(text=err)
            return
        self.app.images.forget(self.key)
        self.app.refresh_meta_views([self.key])
        if self.winfo_exists():
            self.destroy()
