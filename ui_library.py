"""ROM Library window: browse a central ROM library, check games out to this machine and back in."""
from __future__ import annotations

import os
import platform
import sys
import queue as pyqueue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import apptheme
import cloudsync
import libmeta as M
import libqueue as Q
import romlibrary as L
import ui_covers as C

APP_NAME = "ROM Library"
SYSTEM = platform.system()
ALL = "__all__"
FILTERS = ("All games", "On this machine", "Not on this machine", "Checked out elsewhere", "Favourites",
           "Only on this machine")
COLORS = dict(apptheme.STATUS)        # readable on the ROM Library's dark theme


def fmt_eta(sec):
    if sec is None:
        return ""
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m left"
    return f"{sec // 60}:{sec % 60:02d} left"


class TouchScroll:
    """Dragging a finger over a list scrolls it (touchscreens send no wheel events).  A tap that
    doesn't move still selects as usual; on_tap(event) is called for taps if given."""
    STEP = 24

    def __init__(self, tree, on_tap=None):
        self.tree, self.on_tap, self.d = tree, on_tap, None
        tree.bind("<ButtonPress-1>", self._press, add="+")
        tree.bind("<B1-Motion>", self._drag, add="+")
        tree.bind("<ButtonRelease-1>", self._release, add="+")
        for seq, step in (("<Button-4>", -1), ("<Button-5>", 1)):
            tree.bind(seq, lambda e, s=step: (tree.yview_scroll(s, "units"), "break")[1])
        tree.bind("<MouseWheel>", lambda e: (tree.yview_scroll(-1 if e.delta > 0 else 1, "units"), "break")[1])

    def _press(self, e):
        self.d = {"y": e.y, "moved": False, "heading": self.tree.identify_region(e.x, e.y) == "heading"}

    def _drag(self, e):
        d = self.d
        if not d or d["heading"]:
            return
        dy = e.y - d["y"]
        if abs(dy) >= self.STEP:
            rows = int(dy / self.STEP)
            self.tree.yview_scroll(-rows, "units")
            d["y"] += rows * self.STEP
            d["moved"] = True
        if d["moved"]:
            return "break"

    def _release(self, e):
        if self.d and not self.d["moved"] and not self.d["heading"] and self.on_tap:
            self.on_tap(e)

    @property
    def dragged(self):
        return bool(self.d and self.d["moved"])


class SaveManagerLink:
    """What the BIOS Vault needs from the Emulator Save Manager - its settings (the vault and its
    cloud sync live there), the emulators' folders and the backup folder - without its window."""

    def __init__(self):
        import autosync
        self.cfg, self.profiles, self._resolve = autosync.load_app()

    @property
    def backup_dir(self):
        return Path(self.cfg["backup_dir"])

    def resolve(self, emu):
        return self._resolve(emu)

    def save_cfg(self):
        import emu_save_manager as esm
        esm.save_config(self.cfg)

    def open_folder(self, path):
        import emu_save_manager as esm
        esm.open_folder(path)


class LibraryApp(tk.Tk):
    def __init__(self, cfg=None, autostart=True):
        super().__init__()
        self.title(APP_NAME)
        self.cfg = cfg if cfg is not None else L.load_config()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.compact = sh < 900
        self.geometry(f"{min(1180, sw - 20)}x{min(760, sh - 70)}")
        self.minsize(min(900, sw - 20), min(560, sh - 70))
        if self.compact and SYSTEM == "Linux":
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass
        apptheme.apply_library(self)
        self._set_icon()
        self.lib = None
        self.q = None
        self.events = pyqueue.Queue()        # work done in threads -> handled on the Tk thread
        self.sort_col, self.sort_rev = "system", False
        self.system_sel = ALL
        self._q_seen = -1
        self._job_states = {}
        self._indexing = False
        self.meta = None                     # game information and artwork (libmeta.MetaStore)
        self.view = self.cfg.get("view", "covers")
        self.tile_w = 140 if self.compact else 150
        self.images = C.ImageCache(self, self.tile_w, int(self.tile_w * 1.4))
        self.scraper = None
        self.scrape_status = ""
        self.artwork_dialog = None
        self._esde = None
        self.bios = {}                       # system -> libbios.SystemBios
        self._bios_data = None               # (vault entries, emulator targets) on this machine
        self._bios_loading = False
        self._build()
        self.pad = None
        self._start_controller()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self._poll)
        if autostart:
            self.after(100, self.start_library)
            self.after(1500, self._steam_art)

    def _steam_art(self):
        """Give the Steam shortcuts (this one and the Save Manager's) their artwork."""
        try:
            import steamshortcut
            if steamshortcut.refresh_art():
                self.set_status("Added the ROM Library's artwork to its Steam shortcut "
                                "(it shows in the library after Steam restarts).")
        except Exception:  # noqa - artwork must never get in the way
            pass

    def open_bios(self):
        """The BIOS Vault (moved here from the Save Manager; its settings stay in the Save Manager's)."""
        try:
            link = SaveManagerLink()
        except Exception as ex:  # noqa
            messagebox.showerror("BIOS", f"Couldn't read the Save Manager's settings: {ex}", parent=self)
            return None
        from ui_biosvault import BiosVaultDialog
        return BiosVaultDialog(link, master=self)

    def _start_controller(self):
        """Controller navigation in this window (and its questions). Off with "controller": false."""
        if not self.cfg.get("controller", True):
            return
        try:
            import libpad
            self.pad = libpad.LibraryPad(self)
        except Exception as ex:  # noqa - a controller problem must never stop the app
            print("Controller:", ex)
            self.pad = None
            return
        self.pad.watch_hint(self.pad_hint, side="right", padx=(0, 14))

    def open_checkup(self):
        """A report of entries that aren't games, duplicates and formats to convert (changes nothing)."""
        if not self.lib:
            messagebox.showinfo("Check-up", "Set up the library first (Settings…).", parent=self)
            return None
        from ui_libcheck import CheckupDialog
        return CheckupDialog(self)

    def show_keys(self, keys):
        """Show these entries in the main list: every system, no search or filter, selected."""
        self.v_search.set("")
        self.v_filter.set(FILTERS[0])
        systems = {self.lib.get(k).system for k in keys if self.lib.get(k)}
        self.system_sel = systems.pop() if len(systems) == 1 else ALL
        if self.sys_tree.exists(self.system_sel):
            self.sys_tree.selection_set(self.system_sel)
        self.fill_games()
        self._select([k for k in keys if self.lib.get(k)])
        self.lift()

    # ------------------------------------------------------------------ BIOS status
    def load_bios(self, then=None):
        """Read the BIOS Vault and this machine's emulators in the background, then show each
        system's BIOS status in the systems list."""
        if self._bios_loading or not self.lib:
            return
        self._bios_loading = True
        systems = {it.system for it in list(self.lib.items.values()) + list(self.lib.local.values())}

        def work():
            data, statuses = None, {}
            try:
                import libbios
                data = libbios.load(SaveManagerLink())
                statuses = libbios.status(systems, *data)
            except Exception as ex:  # noqa - BIOS status is extra information only
                print("BIOS status:", ex)
            self.events.put(("call", lambda: self._bios_ready(data, statuses, then)))
        threading.Thread(target=work, daemon=True).start()

    def _bios_ready(self, data, statuses, then=None):
        self._bios_loading = False
        if data is not None:
            self._bios_data, self.bios = data, statuses
            if self.lib:
                self.fill_systems()
        if then:
            then()

    def bios_after_checkout(self, systems):
        """A game was checked out: copy its system's BIOS from the vault if this machine lacks it."""
        if not self.cfg.get("bios_on_checkout", True):
            return
        want = [s for s in systems if self.bios.get(s) and self.bios[s].state == "copy"]
        if not want or not self._bios_data:
            return
        entries, targets = self._bios_data

        def work():
            import libbios
            done = []
            for s in want:
                try:
                    c = libbios.install_for(s, entries, targets, log=lambda m: None)
                except Exception as ex:  # noqa
                    self.events.put(("log", f"Couldn't copy the {L.system_name(s)} BIOS: {ex}"))
                    continue
                if c["installed"]:
                    done.append(f"{L.system_name(s)} ({c['installed']} file(s))")
            if done:
                self.events.put(("log", "Copied the BIOS from your BIOS Vault for " + ", ".join(done) + "."))
            self.events.put(("call", self.load_bios))
        threading.Thread(target=work, daemon=True).start()

    def _set_icon(self):
        try:
            import steamshortcut
            art = steamshortcut.art_dir("library")
            self._icons = [tk.PhotoImage(file=str(art / f"icon-{n}.png")) for n in (256, 64, 32, 16)
                           if (art / f"icon-{n}.png").is_file()]
            if self._icons:
                self.iconphoto(True, *self._icons)
        except Exception:  # noqa
            pass

    # ------------------------------------------------------------------ layout
    def _build(self):
        style = ttk.Style(self)
        rowh = 30 if self.compact else 22
        style.configure("Lib.Treeview", rowheight=rowh)
        if self.compact:
            style.configure("Lib.Vertical.TScrollbar", arrowsize=26, width=26)
        sbstyle = "Lib.Vertical.TScrollbar" if self.compact else "Vertical.TScrollbar"

        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Library:").pack(side="left")
        self.loc_label = ttk.Label(top, text="(not set)", foreground=apptheme.DIM)
        self.loc_label.pack(side="left", padx=(4, 12))
        ttk.Button(top, text="Settings…", command=self.open_settings).pack(side="right")
        ttk.Button(top, text="BIOS…", command=self.open_bios).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Check-up…", command=self.open_checkup).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Artwork…", command=self.open_artwork).pack(side="right", padx=(0, 8))
        self.refresh_btn = ttk.Button(top, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="right", padx=(0, 8))

        bar = ttk.Frame(self, padding=(10, 0, 10, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Search:").pack(side="left")
        self.v_search = tk.StringVar()
        self.v_search.trace_add("write", lambda *a: self._schedule_fill())
        e = ttk.Entry(bar, textvariable=self.v_search, width=26)
        e.pack(side="left", padx=(4, 14))
        ttk.Label(bar, text="Show:").pack(side="left")
        self.v_filter = tk.StringVar(value=FILTERS[0])
        cb = ttk.Combobox(bar, textvariable=self.v_filter, values=FILTERS, state="readonly", width=22)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.fill_games())
        ttk.Label(bar, text="View:").pack(side="left", padx=(14, 4))
        self.v_view = tk.StringVar(value=self.view)
        for val, text in (("covers", "Covers"), ("list", "List")):
            ttk.Radiobutton(bar, text=text, value=val, variable=self.v_view, style="Toolbutton",
                            command=lambda: self.set_view(self.v_view.get())).pack(side="left")
        self.count_label = ttk.Label(bar, text="", foreground=apptheme.DIM)
        self.count_label.pack(side="right")
        self.pad_hint = ttk.Label(bar, text="", foreground=apptheme.ACCENT)   # shown with a controller

        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10)
        left = ttk.Frame(pane)
        self.sys_tree = ttk.Treeview(left, columns=("name", "count", "bios"), show="headings",
                                     selectmode="browse", style="Lib.Treeview")
        self.sys_tree.heading("name", text="System")
        self.sys_tree.heading("count", text="Games")
        self.sys_tree.heading("bios", text="BIOS")
        self.sys_tree.column("name", width=110, minwidth=80, stretch=True)
        self.sys_tree.column("count", width=56, anchor="e", stretch=False)
        self.sys_tree.column("bios", width=46, anchor="center", stretch=False)
        self.sys_tree.tag_configure("bios_missing", foreground=apptheme.AMBER)
        ssb = ttk.Scrollbar(left, orient="vertical", command=self.sys_tree.yview, style=sbstyle)
        self.sys_tree.configure(yscrollcommand=ssb.set)
        ssb.pack(side="right", fill="y")
        self.sys_tree.pack(side="left", fill="both", expand=True)
        self.sys_tree.bind("<<TreeviewSelect>>", self._on_system)
        self.sys_tree.bind("<Button-3>", self._system_context)
        TouchScroll(self.sys_tree)
        pane.add(left, weight=1)

        right = ttk.Frame(pane)
        self.list_frame = ttk.Frame(right)
        cols = ("fav", "title", "system", "size", "status")
        self.tree = ttk.Treeview(self.list_frame, columns=cols, show="headings", selectmode="extended",
                                 style="Lib.Treeview")
        for c, text, w, anchor in (("fav", "★", 34, "center"), ("title", "Game", 360, "w"),
                                   ("system", "System", 130, "w"), ("size", "Size", 90, "e"),
                                   ("status", "Status", 260, "w")):
            self.tree.heading(c, text=text, command=lambda c=c: self._sort(c))
            self.tree.column(c, width=w, anchor=anchor, stretch=c in ("title", "status"))
        for tag, color in COLORS.items():
            self.tree.tag_configure(tag, foreground=color)
        gsb = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.tree.yview, style=sbstyle)
        self.tree.configure(yscrollcommand=gsb.set)
        gsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_selection())
        self.tree.bind("<Double-1>", lambda e: self.show_details())
        self.tree.bind("<Button-3>", self._games_context)
        self.games_touch = TouchScroll(self.tree)
        self.grid_view = C.CoverGrid(right, self, self.tile_w)
        self.grid_view.canvas.bind("<Button-3>", self._games_context)
        pane.add(right, weight=4)
        self.details_w = 300 if self.compact else 340
        self.details = C.DetailsPanel(pane, self, width=self.details_w)
        pane.add(self.details, weight=0)
        self.pane = pane
        self.set_view(self.view, refill=False)
        self.after(150, self._place_sashes)

        act = ttk.Frame(self, padding=(10, 6))
        act.pack(fill="x")
        self.act = act
        self.btn_out = ttk.Button(act, text="⬇ Check out", command=self.check_out)
        self.btn_in = ttk.Button(act, text="⬆ Check in", command=self.check_in)
        self.btn_add = ttk.Button(act, text="Add to library", command=self.add_to_library)
        self.btn_fav = ttk.Button(act, text="★ Favourite", command=self.toggle_favourite)
        self.btn_info = ttk.Button(act, text="Details…", command=self.show_details)
        for b in (self.btn_out, self.btn_in, self.btn_add, self.btn_fav, self.btn_info):
            b.pack(side="left", padx=(0, 8))
        self.sel_label = ttk.Label(act, text="", foreground=apptheme.DIM)
        self.sel_label.pack(side="left", padx=8)

        tf = ttk.LabelFrame(self, text="Transfers", padding=6)
        tf.pack(fill="x", padx=10)
        self.tf = tf
        qcols = ("game", "action", "progress", "status")
        self.qtree = ttk.Treeview(tf, columns=qcols, show="headings", height=3 if self.compact else 4,
                                  selectmode="browse", style="Lib.Treeview")
        for c, text, w in (("game", "Game", 360), ("action", "Action", 120), ("progress", "Progress", 90),
                           ("status", "Status", 360)):
            self.qtree.heading(c, text=text)
            self.qtree.column(c, width=w, anchor="e" if c == "progress" else "w", stretch=c in ("game", "status"))
        for tag, color in (("done", COLORS["here"]), ("failed", COLORS["failed"]), ("running", COLORS["busy"]),
                           ("cancelled", COLORS["elsewhere"])):
            self.qtree.tag_configure(tag, foreground=color)
        qsb = ttk.Scrollbar(tf, orient="vertical", command=self.qtree.yview, style=sbstyle)
        self.qtree.configure(yscrollcommand=qsb.set)
        qbtn = ttk.Frame(tf)
        qbtn.pack(side="right", fill="y", padx=(8, 0))
        ttk.Button(qbtn, text="Cancel", command=self.cancel_job).pack(fill="x")
        ttk.Button(qbtn, text="Cancel all", command=lambda: self.q and self.q.cancel_all()).pack(fill="x", pady=4)
        ttk.Button(qbtn, text="Clear finished", command=lambda: self.q and self.q.clear_finished()).pack(fill="x")
        qsb.pack(side="right", fill="y")
        self.qtree.pack(side="left", fill="x", expand=True)
        self.qtree.bind("<Double-1>", self._job_details)
        TouchScroll(self.qtree)

        bottom = ttk.Frame(self, padding=(10, 6, 10, 8))
        bottom.pack(fill="x")
        self.bottom = bottom
        self.space_bar = ttk.Progressbar(bottom, length=180, maximum=100)
        self.space_bar.pack(side="left")
        self.space_label = ttk.Label(bottom, text="")
        self.space_label.pack(side="left", padx=8)
        self.status = ttk.Label(bottom, text="", foreground=apptheme.DIM)
        self.status.pack(side="right")
        self._update_selection()
        self._arrange()

    def _arrange(self):
        """Hide what isn't needed: the button row in the cover view (the details panel has the buttons),
        and the transfers panel while nothing is queued."""
        busy = bool(self.q and self.q.snapshot())
        for w in (self.act, self.tf):
            w.pack_forget()
        if self.view == "list":
            self.act.pack(fill="x", before=self.bottom)
        if busy:
            self.tf.pack(fill="x", padx=10, before=self.bottom)

    def _place_sashes(self):
        try:
            self.pane.sashpos(0, 230 if self.compact else 260)
            w = self.pane.winfo_width()
            if w > 600:
                self.pane.sashpos(1, w - self.details_w - 20)
        except tk.TclError:
            pass

    def set_view(self, view, refill=True):
        keep = self.selected() if refill else []
        self.view = view
        self.v_view.set(view)
        if view == "covers":
            self.list_frame.pack_forget()
            self.grid_view.pack(fill="both", expand=True)
        else:
            self.grid_view.pack_forget()
            self.list_frame.pack(fill="both", expand=True)
        if self.cfg.get("view") != view:
            self.cfg["view"] = view
            try:
                L.save_config(self.cfg)
            except OSError:
                pass
        if hasattr(self, "bottom"):
            self._arrange()
        if refill:
            self.fill_games()
            self._select(keep)

    def _select(self, keys):
        if self.view == "covers":
            self.grid_view.set_selection(keys)
            if keys:
                self.grid_view.see(keys[0])
        else:
            keys = [k for k in keys if self.tree.exists(k)]
            self.tree.selection_set(keys)
            if keys:
                self.tree.see(keys[0])
        self._update_selection()

    # ------------------------------------------------------------------ artwork
    def display_title(self, key):
        rec = self.meta.get(key) if self.meta else {}
        return rec.get("title") or self.lib.get(key).title

    def active_job(self, key):
        return self._active_job(key)

    def on_image(self, key):
        if self.view == "covers":
            self.grid_view.draw()
        if self.details.key == key:
            self.details.show([key])

    def on_grid_select(self):
        self._update_selection()

    def on_grid_double(self):
        keys = self.selected()
        if len(keys) == 1:
            self.show_details()

    def esde_source(self):
        if self._esde is None:
            self._esde = M.EsdeSource()
        return self._esde

    def can_match(self):
        return M.ScreenScraperSource(M.load_ss_creds()).available

    def open_artwork(self):
        if not self.lib:
            return
        if self.artwork_dialog and self.artwork_dialog.winfo_exists():
            self.artwork_dialog.lift()
            return
        C.ArtworkDialog(self)

    # ------------------------------------------------------------------ right-click menus
    def popup(self, event, items, title=""):
        """A right-click menu of [(label, function)] at the mouse."""
        if not items:
            return None
        m = tk.Menu(self, tearoff=False, background=apptheme.PANEL, foreground=apptheme.TEXT,
                    activebackground=apptheme.ACCENT_DARK, activeforeground="#ffffff",
                    disabledforeground=apptheme.DIM, relief="flat", borderwidth=1)
        if title:
            m.add_command(label=title, state="disabled")
            m.add_separator()
        for label, func in items:
            m.add_command(label=label, command=func)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return m

    def _games_context(self, event):
        """Right-click on a game: select it (unless it's already among the selected), then its menu."""
        import libpad
        if not self.lib:
            return "break"
        if self.view == "covers":
            g = self.grid_view
            key = g._tile_at(event.x, event.y)
            if key is None:
                return "break"
            if key not in g.sel:
                g.set_selection([key])
            g.anchor = key
            self.on_grid_select()
        else:
            key = self.tree.identify_row(event.y)
            if not key:
                return "break"
            if key not in self.tree.selection():
                self.tree.selection_set(key)
            self.tree.focus(key)
        keys = self.selected()
        if keys:
            self.popup(event, libpad.game_items(self, keys), libpad.game_title(self, keys))
        return "break"

    def _system_context(self, event):
        import libpad
        row = self.sys_tree.identify_row(event.y)
        if not row:
            return "break"
        if row not in self.sys_tree.selection():
            self.sys_tree.selection_set(row)
            self._on_system()
        self.popup(event, libpad.system_items(self, row), libpad.system_title(row) if row != ALL else "")
        return "break"

    # ------------------------------------------------------------------ artwork
    def scrape_skip(self) -> set:
        """Systems left out when looking up artwork for all games."""
        return set(self.cfg.get("artwork", {}).get("skip_systems", []))

    def set_scrape_skip(self, systems):
        self.cfg.setdefault("artwork", {})["skip_systems"] = sorted(set(systems))
        L.save_config(self.cfg)

    def toggle_scrape_skip(self, system):
        skip = self.scrape_skip()
        skip ^= {system}
        self.set_scrape_skip(skip)
        name = L.system_name(system)
        self.set_status(f"{name} is left out of artwork look-ups for all games." if system in skip
                        else f"{name} is included in artwork look-ups again.")

    def scrape_system(self, system):
        """Look up what's missing for one system, with the sources chosen in Artwork…"""
        if self.scraping():
            self.set_status("Artwork is already being looked up; wait for it or stop it in Artwork…")
            return
        cfg = self.cfg.get("artwork", {})
        keys = [k for k, it in self.lib.items.items() if it.system == system]
        self.start_scrape(keys, esde=cfg.get("esde", True), ss=cfg.get("screenscraper", True),
                          lr=cfg.get("libretro", True))

    def choose_picture(self, key, kind="cover"):
        """Use a picture file from this machine as a game's cover (or screenshot), for every machine."""
        it = self.lib.get(key) if self.lib else None
        if not it or not self.meta:
            return
        what = "cover" if kind == "cover" else "screenshot"
        path = filedialog.askopenfilename(
            parent=self, title=f"Choose a {what} picture for {it.title}",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*")])
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as ex:
            messagebox.showerror(APP_NAME, f"Couldn't read the picture: {ex}", parent=self)
            return
        if M.normalise_image(data, kind)[0] is None:
            messagebox.showerror(APP_NAME, "That file isn't a picture this app can read.", parent=self)
            return
        self.set_status(f"Saving the new {what} for {it.title}…")

        def work():
            mine = sorted(set(self.meta.get(key).get("custom", [])) | {kind})
            self.meta.put(key, {"custom": mine}, {kind: data}, replace=True)   # only "custom" and the picture
            self.meta.flush_async(info_systems=[it.system])
            self.meta.wait()
            ok = self.meta.write_ok

            def done():
                self.images.forget(key)
                self.refresh_meta_views([key])
                self.set_status(f"New {what} saved for {it.title}." if ok else
                                f"The new {what} shows here; it couldn't be saved to the library yet "
                                "(it will be with the next artwork save).")
            self.events.put(("call", done))
        threading.Thread(target=work, daemon=True).start()

    def scraping(self):
        return bool(self.scraper and self.scraper.thread and self.scraper.thread.is_alive())

    def start_scrape(self, keys, esde=True, ss=True, lr=True, refresh=False):
        if self.scraping() or not self.meta:
            return
        self._esde = None                      # look again: the user may have just scraped in ES-DE
        sources = []
        if esde:
            sources.append(self.esde_source())
        if ss:
            sources.append(M.ScreenScraperSource(M.load_ss_creds()))
        if lr:
            sources.append(M.LibretroSource())
        if not any(src.available for src in sources):
            messagebox.showinfo(APP_NAME, "No source is available: switch one on, or enter your ScreenScraper "
                                "details.", parent=self)
            return

        def progress(done, total, msg):
            self.events.put(("scrape", done, total, msg))
        self.scraper = M.Scraper(self.meta, sources, keys, progress=progress, refresh=refresh).start()

    def stop_scrape(self):
        if self.scraper:
            self.scraper.cancel()

    def _scrape_progress(self, done, total, msg):
        self.scrape_status = f"Artwork: {done} of {total} · {msg}" if done < total else msg
        self.set_status(self.scrape_status)
        if self.artwork_dialog and self.artwork_dialog.winfo_exists():
            self.artwork_dialog.update_progress(done, total, msg)
        if done == total or done % 10 == 0:
            self.images.forget()
            self.refresh_meta_views()

    def refresh_meta_views(self, keys=None):
        if self.view == "covers":
            self.grid_view.draw()
        else:
            self.refresh_rows(keys)
        self.details.show(self.selected())

    # ------------------------------------------------------------------ library
    def start_library(self):
        if self.q:
            self.q.stop(wait=3)
            self.q = None
        loc = self.cfg.get("library", "")
        self.loc_label.config(text=loc or "(not set: choose one in Settings)")
        if not loc:
            self.set_status("Choose where your ROM library is.")
            self.after(50, self.open_settings)
            return
        exe = cloudsync.find_rclone(self.cfg.get("rclone", ""))
        if not exe:
            self.set_status("rclone wasn't found. Open Settings to download it.")
            self.after(50, self.open_settings)
            return
        runner = cloudsync.RcloneRunner(exe, L.rclone_conf(), lambda m: None)
        local = self.cfg.get("local_roms") or L.default_local_roms()
        self.lib = L.Library(runner, loc, local, label=self.cfg.get("machine_label") or "")
        self.meta = M.MetaStore(self.lib)
        self.meta.load_local_copy()
        self.images.forget()
        if self.lib.load_cache():
            self.lib.refresh_local()
            self.fill_all()
            self.set_status(f"Showing the library as of {time.strftime('%d %b %H:%M', time.localtime(self.lib.indexed_at))}; refreshing…")
        self.q = Q.TransferQueue(self.lib, log=lambda m: self.events.put(("log", m)))
        self.refresh()

    def refresh(self):
        if not self.lib or self._indexing:
            return
        self._indexing = True
        self.refresh_btn.config(state="disabled")
        self.set_status("Reading the library… (a big library can take a minute or two)")
        lib = self.lib

        meta = self.meta

        def work():
            try:
                lib.refresh()
                try:
                    meta.load()
                except Exception:  # noqa - artwork is optional
                    pass
                self.events.put(("indexed", lib, ""))
            except Exception as ex:  # noqa
                self.events.put(("indexed", lib, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def _indexed(self, lib, err):
        self._indexing = False
        self.refresh_btn.config(state="normal")
        if lib is not self.lib:
            return
        if err:
            self.set_status("Couldn't read the library.")
            messagebox.showerror(APP_NAME, f"Couldn't read the library at\n{lib.location}\n\n{err}\n\n"
                                 "Check that the drive or share is reachable, and the location in Settings.",
                                 parent=self)
            return
        self.images.forget()                   # pictures may have arrived from another machine
        self.fill_all()
        n = len(lib.items)
        self.set_status(f"Library read: {n} games.")
        self.load_bios()

    def fill_all(self):
        self.fill_systems()
        self.fill_games()
        self.update_space()

    # ------------------------------------------------------------------ systems list
    def fill_systems(self):
        lib = self.lib
        counts, here = {}, {}
        for k in lib.all_keys():
            it = lib.get(k)
            counts[it.system] = counts.get(it.system, 0) + 1
            if lib.status(k)[0] in ("here", "partial", "local_only", "card_out"):
                here[it.system] = here.get(it.system, 0) + 1
        self.sys_tree.delete(*self.sys_tree.get_children())
        total, total_here = sum(counts.values()), sum(here.values())
        self.sys_tree.insert("", "end", iid=ALL, values=("All systems", self._count_text(total, total_here), ""))
        for s in sorted(counts, key=lambda s: L.system_name(s).lower()):
            sb = self.bios.get(s)
            badge = sb.badge() if sb else ""
            tags = ("bios_missing",) if sb and sb.state == "missing" and sb.need == "required" else ()
            self.sys_tree.insert("", "end", iid=s, tags=tags,
                                 values=(L.system_name(s), self._count_text(counts[s], here.get(s, 0)), badge))
        if self.system_sel not in counts and self.system_sel != ALL:
            self.system_sel = ALL
        self.sys_tree.selection_set(self.system_sel)

    @staticmethod
    def _count_text(n, h):
        return f"{h} of {n}" if h else str(n)          # how many are on this machine, of all

    def _on_system(self, _e=None):
        sel = self.sys_tree.selection()
        new = sel[0] if sel else ALL
        if new != self.system_sel:
            self.system_sel = new
            self.fill_games()
            sb = self.bios.get(new)
            if self.lib and new in self.lib.offline:
                sysdir, target = self.lib.offline[new]
                self.set_status(f"{L.system_name(new)}'s folder ({sysdir}) is a link to {target}, which isn't "
                                f"there: insert that SD card to use these games.")
            elif sb and sb.need:
                self.set_status(sb.text(L.system_name(new)))

    # ------------------------------------------------------------------ games list
    def _schedule_fill(self):
        if getattr(self, "_fill_after", None):
            self.after_cancel(self._fill_after)
        self._fill_after = self.after(250, self.fill_games)

    def _row(self, key):
        """(values, tags) for a game."""
        lib = self.lib
        it = lib.get(key)
        st, detail = lib.status(key)
        elsewhere = lib.elsewhere(key)
        tags = []
        if st == "here":
            text, tags = "✔ On this machine", ["here"]
        elif st == "partial":
            text, tags = f"◐ Incomplete ({detail})", ["partial"]
        elif st == "local_only":
            text, tags = "Only on this machine", ["local_only"]
        elif st == "card_out":
            text, tags = "▣ On an SD card that isn't inserted", ["card_out"]
        elif elsewhere:
            text, tags = "In library · on " + ", ".join(elsewhere), ["elsewhere"]
        else:
            text = "In library"
        if elsewhere and st in ("here", "partial"):
            text += " · also on " + ", ".join(elsewhere)
        job = self._active_job(key)
        if job:
            if job.state == "running":
                verb = {"checkout": "Copying", "add": "Adding to library", "checkin": "Checking in",
                        "remove": "Removing"}[job.kind]
                text = f"{verb} {job.percent}%" + (f" · {L.human(job.speed)}/s" if job.speed else "") + \
                       (f" · {fmt_eta(job.eta)}" if job.eta else "")
            else:
                text = f"Waiting to {Q.KINDS[job.kind].lower()}"
            tags = ["busy"]
        fav = "★" if lib.is_favourite(key) else ""
        return (fav, it.title, L.system_name(it.system), L.human(it.size), text), tags

    def _active_job(self, key):
        if not self.q:
            return None
        for j in self.q.snapshot():
            if j.key == key and j.state in ("queued", "running"):
                return j
        return None

    def _matches(self, key, flt, words):
        lib = self.lib
        it = lib.get(key)
        if self.system_sel != ALL and it.system != self.system_sel:
            return False
        if words:
            rec = self.meta.get(key) if self.meta else {}
            hay = " ".join((it.title, it.path, rec.get("title", ""), rec.get("genre", ""),
                            rec.get("developer", ""), rec.get("publisher", ""), rec.get("year", ""))).lower()
            if not all(w in hay for w in words):
                return False
        if flt == FILTERS[0]:
            return True
        st = lib.status(key)[0]
        if flt == "On this machine":
            return st in ("here", "partial", "local_only", "card_out")
        if flt == "Not on this machine":
            return st == "not_here"
        if flt == "Checked out elsewhere":
            return bool(lib.elsewhere(key))
        if flt == "Favourites":
            return lib.is_favourite(key)
        if flt == "Only on this machine":
            return st == "local_only"
        return True

    def fill_games(self):
        self._fill_after = None
        if not self.lib:
            return
        lib = self.lib
        flt = self.v_filter.get()
        words = [w for w in self.v_search.get().lower().split() if w]
        keys = [k for k in lib.all_keys() if self._matches(k, flt, words)]
        keys = self._sorted(keys)
        sel = set(self.selected())
        self.tree.delete(*self.tree.get_children())
        if self.view == "list":
            for k in keys:
                values, tags = self._row(k)
                self.tree.insert("", "end", iid=k, values=values, tags=tags)
            keep = [k for k in keys if k in sel]
            if keep:
                self.tree.selection_set(keep)
        else:
            self.grid_view.set_keys(keys)
            self.grid_view.set_selection([k for k in keys if k in sel], notify=False)
        total = sum(lib.get(k).size for k in keys)
        self.count_label.config(text=f"{len(keys)} games · {L.human(total)}")
        self._update_selection()

    def _sorted(self, keys):
        lib = self.lib
        col = self.sort_col
        if col == "size":
            f = lambda k: lib.get(k).size
        elif col == "title":
            f = lambda k: self.display_title(k).lower()
        elif col == "status":
            order = {"here": 0, "partial": 1, "card_out": 2, "local_only": 3, "not_here": 4}
            f = lambda k: (order[lib.status(k)[0]], lib.get(k).title.lower())
        elif col == "fav":
            f = lambda k: (not lib.is_favourite(k), lib.get(k).title.lower())
        else:
            f = lambda k: (L.system_name(lib.get(k).system).lower(), self.display_title(k).lower())
        return sorted(keys, key=f, reverse=self.sort_rev)

    def _sort(self, col):
        if self.games_touch.dragged:
            return
        self.sort_rev = (not self.sort_rev) if self.sort_col == col else (col == "size")
        self.sort_col = col
        self.fill_games()

    def refresh_rows(self, keys=None):
        if not self.lib:
            return
        if self.view == "covers":
            self.grid_view.draw()
            if self.details.key and (keys is None or self.details.key in keys):
                self.details.show([self.details.key])
            return
        for k in (keys if keys is not None else self.tree.get_children()):
            if self.tree.exists(k) and self.lib.get(k):
                values, tags = self._row(k)
                self.tree.item(k, values=values, tags=tags)

    def selected(self):
        keys = self.grid_view.sel if self.view == "covers" else self.tree.selection()
        return [k for k in keys if self.lib and self.lib.get(k)]

    def _update_selection(self):
        keys = self.selected()
        lib = self.lib
        if lib:
            self.details.show(keys)
        if not keys:
            self.sel_label.config(text="Select games (Ctrl or Shift for several)")
            for b in (self.btn_out, self.btn_in, self.btn_add, self.btn_fav, self.btn_info):
                b.state(["disabled"])
            return
        sts = [lib.status(k)[0] for k in keys]
        size = sum(lib.get(k).size for k in keys)
        self.sel_label.config(text=f"{len(keys)} selected · {L.human(size)}")
        self.btn_out.state(["!disabled"] if any(s in ("not_here", "partial") for s in sts) else ["disabled"])
        self.btn_in.state(["!disabled"] if any(s in ("here", "partial") for s in sts) else ["disabled"])
        self.btn_add.state(["!disabled"] if any(s == "local_only" for s in sts) else ["disabled"])
        self.btn_fav.state(["!disabled"] if any(k in lib.items for k in keys) else ["disabled"])
        self.btn_info.state(["!disabled"] if len(keys) == 1 else ["disabled"])

    # ------------------------------------------------------------------ actions
    def check_out(self):
        lib = self.lib
        keys = [k for k in self.selected() if lib.status(k)[0] in ("not_here", "partial") and k in lib.items]
        if not keys:
            return
        if not lib.local_roms:
            messagebox.showinfo(APP_NAME, "Set this machine's ROM folder in Settings first.", parent=self)
            return
        out = sorted({lib.items[k].system for k in keys if lib.items[k].system in lib.offline})
        if out:
            cards = "\n".join(f"  {L.system_name(x)}: {lib.offline[x][0]} → {lib.offline[x][1]}" for x in out)
            messagebox.showwarning(APP_NAME, "These systems' folders are links to an SD card that isn't inserted:"
                                   f"\n\n{cards}\n\nInsert the card, then check the games out.", parent=self)
            return
        need = sum(lib.items[k].size - (lib.local[k].size if k in lib.local else 0) for k in keys)
        # each system's games go to the drive its folder is on (a linked folder can be on an SD card)
        per_drive = {}                       # device -> [need, pending, free, label]
        for k in keys:
            system = lib.items[k].system
            dev, mount = L.drive_of(lib.system_path(system))
            sp = lib.system_space(system)
            if dev is None or not sp:
                continue
            d = per_drive.setdefault(dev, [0, 0, sp[2], L.drive_label(mount, dev)])
            d[0] += lib.items[k].size - (lib.local[k].size if k in lib.local else 0)
        for j in self.q.snapshot():
            if j.kind == "checkout" and j.state in ("queued", "running"):
                dev, _m = L.drive_of(lib.system_path(j.key.split("/", 1)[0]))
                if dev in per_drive:
                    per_drive[dev][1] += max(0, j.size - j.done)
        for d_need, pending, free, label in per_drive.values():
            if d_need + pending + Q.SPACE_MARGIN > free:
                where = "this machine" if label == "internal" else f"the SD card \"{label}\""
                messagebox.showwarning(APP_NAME, f"Not enough space on {where}.\n\nThese games need "
                                       f"{L.human(d_need)}" + (f" (plus {L.human(pending)} already queued)" if pending else "")
                                       + f", and {L.human(free)} is free.\n\nCheck some games in first to make room.",
                                       parent=self)
                return
        self.q.add("checkout", keys)
        self.set_status(f"Queued {len(keys)} game(s) to check out ({L.human(need)}).")
        self.refresh_rows(keys)

    def check_in(self):
        lib = self.lib
        keys = self.selected()
        here = [k for k in keys if lib.status(k)[0] == "here"]
        partial = [k for k in keys if lib.status(k)[0] == "partial"]
        local_only = [k for k in keys if lib.status(k)[0] == "local_only"]
        if not here and not partial:
            if local_only:
                messagebox.showinfo(APP_NAME, "These games aren't in the library, so checking them in would "
                                    "lose them. Use Add to library first.", parent=self)
            return
        size = sum(lib.local[k].size for k in here + partial)
        msg = (f"Remove {len(here) + len(partial)} game(s) ({L.human(size)}) from this machine?\n\n"
               "The library keeps its copy, and the app checks it's complete before removing anything. "
               "Your saves are separate and aren't touched.")
        if partial:
            msg += f"\n\n{len(partial)} of them are incomplete copies; the library has the whole game."
        if local_only:
            msg += f"\n\n{len(local_only)} selected game(s) aren't in the library and will be left alone."
        if not messagebox.askokcancel(APP_NAME, msg, parent=self):
            return
        self.q.add("checkin", here)
        self.q.add("remove", partial)
        self.refresh_rows(here + partial)

    def add_to_library(self):
        lib = self.lib
        keys = [k for k in self.selected() if lib.status(k)[0] == "local_only"]
        if not keys:
            return
        size = sum(lib.local[k].size for k in keys)
        if not messagebox.askokcancel(APP_NAME, f"Copy {len(keys)} game(s) ({L.human(size)}) from this machine "
                                      f"into the library?\n\nThey stay on this machine too.", parent=self):
            return
        self.q.add("add", keys)
        self.refresh_rows(keys)

    def toggle_favourite(self):
        lib = self.lib
        keys = [k for k in self.selected() if k in lib.items]
        if not keys:
            return
        on = not all(lib.is_favourite(k) for k in keys)

        def work():
            ok = lib.set_favourite(keys, on)
            self.events.put(("fav", keys, ok, lib.error))
        threading.Thread(target=work, daemon=True).start()

    def show_details(self):
        keys = self.selected()
        if len(keys) != 1:
            return
        DetailsDialog(self, keys[0])

    def cancel_job(self):
        sel = self.qtree.selection()
        if sel and self.q:
            self.q.cancel(sel[0])

    def _job_details(self, _e=None):
        sel = self.qtree.selection()
        if not sel or not self.q:
            return
        for j in self.q.snapshot():
            if j.id == sel[0] and j.error:
                messagebox.showinfo(APP_NAME, f"{Q.KINDS[j.kind]}: {j.title}\n\n{j.error}", parent=self)

    # ------------------------------------------------------------------ transfers, space, status
    def fill_queue(self):
        jobs = self.q.snapshot() if self.q else []
        if bool(jobs) != bool(self.tf.winfo_ismapped()):
            self._arrange()
        have = set(self.qtree.get_children())
        ids = []
        for j in jobs:
            ids.append(j.id)
            if j.state == "running":
                status = (f"{L.human(j.done)} of {L.human(j.size)}" + (f" · {L.human(j.speed)}/s" if j.speed else "")
                          + (f" · {fmt_eta(j.eta)}" if j.eta else ""))
                if j.kind in ("checkin", "remove"):
                    status = "Checking the library's copy, then removing…"
            elif j.state == "queued":
                status = "Waiting" + (" (resumed from last time)" if j.note == "resumed" else "")
            elif j.state == "done":
                status = "Done"
            elif j.state == "failed":
                status = "Failed: " + j.error + "  (double-click for details)"
            else:
                status = "Cancelled"
            prog = f"{j.percent}%" if j.kind in ("checkout", "add") and j.state != "cancelled" else ""
            values = (j.title, Q.KINDS[j.kind], prog, status)
            if j.id in have:
                self.qtree.item(j.id, values=values, tags=(j.state,))
            else:
                self.qtree.insert("", "end", iid=j.id, values=values, tags=(j.state,))
        for iid in have - set(ids):
            self.qtree.delete(iid)

    def update_space(self):
        lib = self.lib
        if not lib or not lib.local_roms:
            self.space_label.config(text="This machine's ROM folder isn't set (Settings).")
            self.space_bar["value"] = 0
            return
        space = L.disk_space(lib.local_roms)
        n, size = lib.checked_out_size()
        drives = lib.drives() if space else []
        if space and len(drives) > 1:           # system folders linked to SD cards
            self.space_bar["value"] = 100 * space[1] / space[0] if space[0] else 0
            parts = " · ".join(f"{L.human(free)} free ({label})" for label, _t, _u, free, _s in drives)
            out = f" · card out: {', '.join(L.system_name(x) for x in sorted(lib.offline))}" if lib.offline else ""
            self.space_label.config(text=f"{lib.label}: {parts} · {n} game(s) checked out ({L.human(size)}){out}")
        elif space:
            total, used, free = space
            self.space_bar["value"] = 100 * used / total if total else 0
            out = f" · card out: {', '.join(L.system_name(x) for x in sorted(lib.offline))}" if lib.offline else ""
            self.space_label.config(text=f"{lib.label}: {L.human(free)} free of {L.human(total)} · "
                                         f"{n} game(s) checked out ({L.human(size)}){out}")
        else:
            self.space_label.config(text=f"{lib.local_roms} isn't reachable.")

    def set_status(self, text):
        self.status.config(text=text)

    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "indexed":
                    self._indexed(ev[1], ev[2])
                elif ev[0] == "scrape":
                    self._scrape_progress(*ev[1:])
                elif ev[0] == "call":
                    ev[1]()
                elif ev[0] == "log":
                    self.set_status(ev[1])
                elif ev[0] == "fav":
                    _, keys, ok, err = ev
                    if not ok:
                        messagebox.showerror(APP_NAME, f"Couldn't save favourites to the library:\n{err}", parent=self)
                    self.refresh_rows(keys)
        except pyqueue.Empty:
            pass
        if self.q and self.q.changed != self._q_seen:
            self._q_seen = self.q.changed
            self.fill_queue()
            changed, finished, checked_out = [], False, set()
            for j in self.q.snapshot():
                prev = self._job_states.get(j.id)
                cur = (j.state, j.percent)
                if prev != cur:
                    changed.append(j.key)
                    if j.state in ("done", "failed", "cancelled") and (not prev or prev[0] != j.state):
                        finished = True
                        if j.state == "done" and j.kind == "checkout":
                            checked_out.add(j.key.split("/", 1)[0])
                    self._job_states[j.id] = cur
            if checked_out:
                self.bios_after_checkout(checked_out)
            if finished:
                self.fill_systems()
                self.fill_games()
                self.update_space()
            elif changed:
                self.refresh_rows(changed)
        self._poll_id = self.after(300, self._poll)

    def destroy(self):
        if getattr(self, "pad", None):
            self.pad.stop()
            self.pad = None
        if getattr(self, "_poll_id", None):
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None
        super().destroy()

    # ------------------------------------------------------------------ settings, closing
    def open_settings(self):
        SettingsDialog(self)

    def apply_settings(self):
        L.save_config(self.cfg)
        self.start_library()

    def on_close(self):
        if self.q and self.q.busy():
            if not messagebox.askokcancel(APP_NAME, "Transfers are still running or waiting.\n\nClose anyway? "
                                          "They'll carry on next time you open the ROM Library.", parent=self):
                return
        if self.q:
            self.q.stop(wait=3)
        if self.scraping():
            self.scraper.cancel()
            self.scraper.thread.join(10)       # it writes what it found before stopping
        self.destroy()


class DetailsDialog(tk.Toplevel):
    def __init__(self, app, key):
        super().__init__(app)
        lib = app.lib
        it = lib.get(key)
        self.title(it.title)
        self.transient(app)
        self.geometry("760x420")
        st, detail = lib.status(key)
        lib_item, loc_item = lib.items.get(key), lib.local.get(key)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        lines = [("Game", it.title), ("System", L.system_name(it.system)), ("Size", L.human(it.size))]
        if lib_item:
            lines.append(("In the library", L.join(lib.location, lib_item.sysdir, lib_item.path)))
        if loc_item:
            lines.append(("On this machine", str(Path(lib.local_roms) / loc_item.sysdir / loc_item.path)))
        other = lib.elsewhere(key)
        if other:
            lines.append(("Also checked out on", ", ".join(other)))
        for i, (a, b) in enumerate(lines):
            ttk.Label(f, text=a + ":", foreground=apptheme.DIM).grid(row=i, column=0, sticky="nw", padx=(0, 8))
            ttk.Label(f, text=b, wraplength=600).grid(row=i, column=1, sticky="w")
        tv = ttk.Treeview(f, columns=("file", "size", "here"), show="headings", height=8)
        for c, t, w in (("file", "File", 470), ("size", "Size", 100), ("here", "Here", 120)):
            tv.heading(c, text=t)
            tv.column(c, width=w, anchor="e" if c == "size" else "w")
        files = (lib_item or loc_item).files
        have = loc_item.files if loc_item else {}
        for rel, (s, _) in sorted(files.items()):
            h = have.get(rel)
            here = "✔" if h and h[0] == s else (f"{L.human(h[0])}" if h else "")
            tv.insert("", "end", values=(rel, L.human(s), here))
        tv.grid(row=len(lines), column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        f.rowconfigure(len(lines), weight=1)
        f.columnconfigure(1, weight=1)
        ttk.Button(f, text="Close", command=self.destroy).grid(row=len(lines) + 1, column=1, sticky="e", pady=(8, 0))


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        cfg = app.cfg
        self.title("ROM Library settings")
        self.transient(app)
        self.resizable(True, False)
        share = cfg.get("share", {})
        loc = cfg.get("library", "")
        self.v_kind = tk.StringVar(value="share" if loc.startswith(L.SHARE_REMOTE + ":") else "folder")
        self.v_folder = tk.StringVar(value="" if self.v_kind.get() == "share" else loc)
        self.v_host = tk.StringVar(value=share.get("host", ""))
        self.v_share = tk.StringVar(value=share.get("share", ""))
        self.v_sub = tk.StringVar(value=share.get("folder", ""))
        self.v_user = tk.StringVar(value=share.get("user", ""))
        self.v_pass = tk.StringVar()
        self.v_local = tk.StringVar(value=cfg.get("local_roms") or L.default_local_roms())
        self.v_label = tk.StringVar(value=cfg.get("machine_label") or L.default_machine_label())
        self.v_rclone = tk.StringVar(value=cloudsync.find_rclone(cfg.get("rclone", "")))

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(f, text="Where is your ROM library?", font=("TkDefaultFont", 11, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w"); r += 1
        ttk.Radiobutton(f, text="A folder: a drive on this machine, or a network path like \\\\NUC\\Games\\roms "
                        "(easiest on Windows)", variable=self.v_kind, value="folder",
                        command=self._kind).grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0)); r += 1
        self.folder_row = ttk.Frame(f)
        self.folder_row.grid(row=r, column=0, columnspan=3, sticky="ew", padx=(24, 0)); r += 1
        ttk.Entry(self.folder_row, textvariable=self.v_folder).pack(side="left", fill="x", expand=True)
        ttk.Button(self.folder_row, text="Browse…",
                   command=lambda: self._browse(self.v_folder)).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(f, text="A network share, signing in with a username and password (best for the "
                        "Steam Deck)", variable=self.v_kind, value="share",
                        command=self._kind).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0)); r += 1
        self.share_box = ttk.Frame(f)
        self.share_box.grid(row=r, column=0, columnspan=3, sticky="ew", padx=(24, 0)); r += 1
        rows = (("Computer name or IP:", self.v_host, "e.g. NUC or 192.168.1.20"),
                ("Share name:", self.v_share, "the shared folder's name, e.g. Games"),
                ("Folder inside the share:", self.v_sub, "optional, e.g. roms"),
                ("Username:", self.v_user, ""),
                ("Password:", self.v_pass, "saved already; leave blank to keep it" if L.share_has_password()
                 else "kept in rclone's config, scrambled"))
        for i, (label, var, hint) in enumerate(rows):
            ttk.Label(self.share_box, text=label).grid(row=i, column=0, sticky="w", pady=1)
            ttk.Entry(self.share_box, textvariable=var, width=30,
                      show="•" if var is self.v_pass else "").grid(row=i, column=1, sticky="w", padx=6)
            ttk.Label(self.share_box, text=hint, foreground=apptheme.DIM).grid(row=i, column=2, sticky="w")
        tb = ttk.Frame(f)
        tb.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0)); r += 1
        ttk.Button(tb, text="Test", command=self.test).pack(side="left")
        self.test_label = ttk.Label(tb, text="", foreground=apptheme.DIM, wraplength=620)
        self.test_label.pack(side="left", padx=8)

        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=10); r += 1
        ttk.Label(f, text="This machine's ROM folder:").grid(row=r, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_local).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="Browse…", command=lambda: self._browse(self.v_local)).grid(row=r, column=2); r += 1
        ttk.Label(f, text="Games are checked out here. EmuDeck's is Emulation/roms; ES-DE and Steam ROM "
                  "Manager find them there.", foreground=apptheme.DIM).grid(row=r, column=1, columnspan=2, sticky="w"); r += 1
        ttk.Label(f, text="This machine's name:").grid(row=r, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(f, textvariable=self.v_label, width=30).grid(row=r, column=1, sticky="w", padx=6, pady=(6, 0)); r += 1
        ttk.Label(f, text="Shown to your other machines, e.g. \"On Steam Deck\".", foreground=apptheme.DIM).grid(
            row=r, column=1, columnspan=2, sticky="w"); r += 1
        ttk.Label(f, text="rclone:").grid(row=r, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(f, textvariable=self.v_rclone).grid(row=r, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(f, text="Download", command=self.download_rclone).grid(row=r, column=2, pady=(6, 0)); r += 1
        ttk.Label(f, text="rclone does the copying (the same one the Emulator Save Manager uses).",
                  foreground=apptheme.DIM).grid(row=r, column=1, columnspan=2, sticky="w"); r += 1
        self.v_bios = tk.BooleanVar(value=bool(cfg.get("bios_on_checkout", True)))
        ttk.Checkbutton(f, text="When I check out a game, copy its system's BIOS from my BIOS Vault into "
                        "this machine's emulators if they don't have it", variable=self.v_bios).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(8, 0)); r += 1

        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=self.save).pack(side="right", padx=(0, 8))
        self._kind()
        self.grab_set()

    def _kind(self):
        share = self.v_kind.get() == "share"
        for w in self.share_box.winfo_children():
            try:
                w.state(["!disabled"] if share else ["disabled"])
            except tk.TclError:
                pass
        for w in self.folder_row.winfo_children():
            w.state(["disabled"] if share else ["!disabled"])

    def _browse(self, var):
        d = filedialog.askdirectory(parent=self, initialdir=var.get() or str(Path.home()))
        if d:
            var.set(str(Path(d)))

    def _form(self):
        """The form's values, read on the Tk thread (background threads must not touch Tk)."""
        return {"kind": self.v_kind.get(), "folder": self.v_folder.get().strip(), "host": self.v_host.get().strip(),
                "share": self.v_share.get().strip(), "sub": self.v_sub.get(), "user": self.v_user.get(),
                "password": self.v_pass.get(), "rclone": self.v_rclone.get().strip()}

    @staticmethod
    def _runner(form):
        exe = form["rclone"] or cloudsync.find_rclone("")
        if not exe or not Path(exe).is_file():
            raise RuntimeError("rclone wasn't found. Click Download, or enter where rclone is.")
        return L.limit_runner(cloudsync.RcloneRunner(exe, L.rclone_conf(), lambda m: None))

    @staticmethod
    def _location(runner, form):
        """Library location from the form (writes the share's rclone remote when that's chosen)."""
        if form["kind"] == "folder":
            if not form["folder"]:
                raise RuntimeError("Enter the library folder.")
            return form["folder"]
        if not form["host"] or not form["share"]:
            raise RuntimeError("Enter the computer's name and the share name.")
        return L.setup_share(runner, form["host"], form["share"], form["sub"], form["user"], form["password"])

    def test(self):
        self.test_label.config(text="Testing…", foreground=apptheme.DIM)
        form = self._form()

        def work():
            try:
                runner = self._runner(form)
                loc = self._location(runner, form)
                rc, out = runner.capture(["lsf", "--dirs-only", "--max-depth", "1", loc], timeout=60, merge=False)
                if rc != 0:
                    raise RuntimeError(L._last_lines(out) or f"rclone exited with {rc}")
                dirs = [d.strip("/") for d in out.splitlines() if d.strip() and not d.startswith(".")]
                known = [d for d in dirs if L.canonical(d) in L.SYSTEMS]
                if known:
                    msg = (f"Connected. Found {len(known)} system folder(s): "
                           + ", ".join(L.system_name(d) for d in known[:8]) + ("…" if len(known) > 8 else ""))
                    color = COLORS["here"]
                elif dirs:
                    msg = ("Connected, but no system folders like ps2 or gc are directly inside. "
                           "Is this the folder that holds them?")
                    color = COLORS["partial"]
                else:
                    msg = "Connected; the folder is empty (fine if you haven't copied the ROMs yet)."
                    color = COLORS["partial"]
            except Exception as ex:  # noqa
                msg, color = f"Couldn't connect: {ex}", COLORS["failed"]
            self.app.events.put(("call", lambda: self.winfo_exists() and
                                 self.test_label.config(text=msg, foreground=color)))
        threading.Thread(target=work, daemon=True).start()

    def download_rclone(self):
        self.v_rclone.set("Downloading…")

        def work():
            try:
                path = str(cloudsync.download_rclone(log=lambda m: None))
            except Exception as ex:  # noqa
                path, err = "", str(ex)
                self.app.events.put(("call", lambda: messagebox.showerror(APP_NAME, f"Download failed: {err}",
                                                                          parent=self.app)))
            self.app.events.put(("call", lambda: self.winfo_exists() and self.v_rclone.set(path)))
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        form = self._form()
        try:
            runner = self._runner(form)
            loc = self._location(runner, form)
        except Exception as ex:  # noqa
            messagebox.showerror(APP_NAME, str(ex), parent=self)
            return
        local = self.v_local.get().strip()
        if L.same_or_nested(loc, local):
            messagebox.showerror(APP_NAME, "The library and this machine's ROM folder must be different folders "
                                 "(and not one inside the other).", parent=self)
            return
        cfg = self.app.cfg
        cfg["library"] = loc
        cfg["local_roms"] = local
        cfg["machine_label"] = self.v_label.get().strip()
        cfg["rclone"] = self.v_rclone.get().strip()
        cfg["bios_on_checkout"] = bool(self.v_bios.get())
        if self.v_kind.get() == "share":
            cfg["share"] = {"host": self.v_host.get().strip(), "share": self.v_share.get().strip(),
                            "folder": self.v_sub.get().strip(), "user": self.v_user.get().strip()}
        self.destroy()
        self.app.apply_settings()


def main():
    app = LibraryApp()
    if "--bios" in sys.argv:                 # opened from the Save Manager's BIOS button
        app.after(300, app.open_bios)
    app.mainloop()


if __name__ == "__main__":
    main()
