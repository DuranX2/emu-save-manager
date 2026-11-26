"""ROM Library: cover grid, game details panel, and the Artwork window (where game information
comes from, and looking it up)."""
from __future__ import annotations

import queue as pyqueue
import threading
import tkinter as tk
from collections import OrderedDict
from tkinter import messagebox, ttk

import libmeta as M
import romlibrary as L

APP_NAME = "ROM Library"
import apptheme

BADGE = {"here": ("✔", "#2e7d32"), "partial": ("◐", "#e65100"), "local_only": ("●", "#1565c0"),
         "busy": ("⬇", "#6a1b9a"), "elsewhere": ("⇄", "#6d6d6d")}


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
        self.cover_h = int(tile_w * 1.4)
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
        img = app.images.get(key)
        if img:
            iw, ih = img.width(), img.height()
            c.create_image(x + self.tw // 2, y + self.cover_h - ih // 2 if ih < self.cover_h else y,
                           image=img, anchor="n" if ih >= self.cover_h else "n")
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
            bx, by = x + self.tw - 16, y + 14
            c.create_oval(bx - 13, by - 13, bx + 13, by + 13, fill=badge[1], outline="white", width=2)
            c.create_text(bx, by, text=badge[0], fill="white", font=("TkDefaultFont", 11, "bold"))
        if lib.is_favourite(key):
            c.create_text(x + 14, y + 14, text="★", fill="#ffd54f", font=("TkDefaultFont", 15, "bold"))
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
        for w in (self.canvas, self.inner):
            w.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
            w.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
            w.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-2 if e.delta > 0 else 2, "units"))
        self.show(None)

    def _clear(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)

    def _label(self, text, **kw):
        lbl = ttk.Label(self.inner, text=text, wraplength=self.pw - 30, **kw)
        lbl.pack(anchor="w", fill="x")
        return lbl

    def show(self, keys):
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
                 "local_only": "Only on this machine (not in the library)", "not_here": "In the library"}[st]
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
        if app.can_match():
            ttk.Button(self.inner, text="Wrong game? Find the right one…",
                       command=lambda: MatchDialog(app, key)).pack(anchor="w", pady=(6, 10))

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
        ttk.Checkbutton(f, text="Look up again even if a game already has information", variable=self.v_refresh).grid(
            row=r, column=0, columnspan=3, sticky="w"); r += 1
        self.progress = ttk.Progressbar(f, maximum=100)
        self.progress.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(10, 2)); r += 1
        self.status = ttk.Label(f, text=app.scrape_status or "", foreground=apptheme.DIM, wraplength=560)
        self.status.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Close", command=self.close).pack(side="right")
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
        self.app.cfg["artwork"] = {"esde": self.v_esde.get(), "screenscraper": self.v_ss.get(),
                                   "libretro": self.v_lr.get()}
        L.save_config(self.app.cfg)

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
            keys = list(app.lib.items)
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
