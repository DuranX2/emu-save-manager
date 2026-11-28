"""ui_cloud.py - window for syncing saves with OneDrive or EmuDeck Cloud in EmuDeck's folder layout."""

import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import autosync
import exitwatch
import cloudsync as cs
import syncplan


class CloudSyncDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Cloud Sync (OneDrive or EmuDeck Cloud, EmuDeck layout)")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.compact = sh < 900                  # Steam Deck (1280x800) and other small screens
        w, h = min(980, sw - 20), min(780, sh - 70)
        self.geometry(f"{w}x{h}")
        self.minsize(min(820, w), min(520, h))
        if self.compact and autosync.SYSTEM != "Windows":
            try:
                self.attributes("-zoomed", True)  # fill the Deck's screen
            except tk.TclError:
                pass
        self.transient(app)
        self.cfg = app.cfg.setdefault("cloud", {})
        self.cfg.setdefault("pairs", {})
        self.q = queue.Queue()
        self.runner = None
        self.busy = False
        self.pairs = {}                       # iid -> dict
        self._ticks = 0
        self._last_banner = None
        self._build()
        self._autodetect()
        self._update_conn_summary(collapse_if_ready=True)
        self._refresh_conflicts(announce=True)
        self._load_pairs()
        self._refresh_auto()
        self.v_base.trace_add("write", lambda *_: [self._refresh_row(k) for k in self.pairs])
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)

    # ---- UI -------------------------------------------------------------------
    def _build(self):
        summary = ttk.Frame(self, padding=(10, 8, 10, 0))
        summary.pack(fill="x")
        self.conn_label = ttk.Label(summary, text="", foreground="#1f5fa8")
        self.conn_label.pack(side="left")
        self.conn_btn = ttk.Button(summary, text="Hide connection settings ▴", command=self._toggle_conn)
        self.conn_btn.pack(side="right")
        self.conn_summary = summary
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        self.conn_frame = top
        self.conn_open = True
        self.v_rclone = tk.StringVar(value=self.cfg.get("rclone", ""))
        self.v_conf = tk.StringVar(value=self.cfg.get("config", ""))
        self.v_remote = tk.StringVar(value=self.cfg.get("remote", ""))
        self.v_base = tk.StringVar(value=self.cfg.get("base", cs.DEFAULT_BASE))
        self.v_dry = tk.BooleanVar(value=False)

        ttk.Label(top, text="rclone program:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self.v_rclone).grid(row=0, column=1, sticky="ew", padx=6)
        bar = ttk.Frame(top)
        bar.grid(row=0, column=2, sticky="e")
        ttk.Button(bar, text="Browse…", command=lambda: self._browse(self.v_rclone)).pack(side="left")
        ttk.Button(bar, text="Download rclone", command=self.download_rclone).pack(side="left", padx=4)

        ttk.Label(top, text="rclone config:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self.v_conf).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse…", command=lambda: self._browse(self.v_conf)).grid(row=1, column=2, sticky="e")

        ttk.Label(top, text="Cloud remote:").grid(row=2, column=0, sticky="w", pady=2)
        rf = ttk.Frame(top)
        rf.grid(row=2, column=1, sticky="w", padx=6)
        self.combo = ttk.Combobox(rf, textvariable=self.v_remote, width=20, state="readonly")
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._on_remote())
        self.remote_kind = ttk.Label(rf, text="", foreground="#666")
        self.remote_kind.pack(side="left", padx=8)
        bar2 = ttk.Frame(top)
        bar2.grid(row=2, column=2, sticky="e")
        ttk.Button(bar2, text="Reload", command=self._reload_remotes).pack(side="left")
        ttk.Button(bar2, text="Set up OneDrive…", command=self.setup_remote).pack(side="left", padx=4)
        ttk.Button(bar2, text="Set up EmuDeck Cloud…", command=self.setup_emudeck_cloud).pack(side="left")

        ttk.Label(top, text="Cloud base folder:").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self.v_base, width=44).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(top, text="Set automatically for EmuDeck Cloud", foreground="#666").grid(row=3, column=2, sticky="e")

        frame = ttk.LabelFrame(self, text="What to sync (tap ✓ / ✗ to switch a row on or off)", padding=6)
        frame.pack(fill="x", padx=10)
        cols = ("sync", "emu", "loc", "local", "cloud")
        style = ttk.Style(self)
        if self.compact:                       # bigger rows and scrollbar for the Deck's touchscreen
            style.configure("Deck.Treeview", rowheight=30)
            style.configure("Deck.Vertical.TScrollbar", arrowsize=26, width=26)
        tf = ttk.Frame(frame)
        tf.pack(fill="x")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=4 if self.compact else 7,
                                 selectmode="browse", style="Deck.Treeview" if self.compact else "Treeview")
        for c, text, w in (("sync", "Sync", 50), ("emu", "Emulator", 100), ("loc", "Location", 110),
                           ("local", "This PC", 340), ("cloud", "Cloud folder", 260)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="center" if c == "sync" else "w")
        self.tree_sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview,
                                     style="Deck.Vertical.TScrollbar" if self.compact else "Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=self.tree_sb.set)
        self.tree_sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="x", expand=True)
        self.tree.bind("<Double-1>", self._toggle)
        self.tree.bind("<ButtonPress-1>", self._tree_press, add="+")
        self.tree.bind("<B1-Motion>", self._tree_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self._tree_release, add="+")
        self.tree.bind("<space>", lambda e: self._toggle_selected())
        self.tree.bind("<Return>", lambda e: self._toggle_selected())
        for seq, step in (("<Button-4>", -1), ("<Button-5>", 1)):          # wheel / trackpad on Linux
            self.tree.bind(seq, lambda e, s=step: (self.tree.yview_scroll(s, "units"), "break")[1])
        self.tree.bind("<MouseWheel>", lambda e: (self.tree.yview_scroll(-1 if e.delta > 0 else 1, "units"),
                                                  "break")[1])
        self._drag = None
        fb = ttk.Frame(frame)
        fb.pack(fill="x", pady=(6, 0))
        ttk.Button(fb, text="Switch on / off", command=self._toggle_selected).pack(side="left")
        ttk.Button(fb, text="All on", command=lambda: self._set_all(True)).pack(side="left", padx=(8, 0))
        ttk.Button(fb, text="All off", command=lambda: self._set_all(False)).pack(side="left", padx=(8, 0))
        ttk.Button(fb, text="Edit cloud folder…", command=self._edit_remote).pack(side="left", padx=(16, 0))

        act = ttk.Frame(self, padding=(10, 8))
        act.pack(fill="x")
        ttk.Checkbutton(act, text="Preview only (change nothing)", variable=self.v_dry).pack(side="left")
        self.buttons = []
        for text, cmd in (("Test connection", self.test), ("Download ⬇ cloud → this PC", lambda: self.start("download")),
                          ("Upload ⬆ this PC → cloud", lambda: self.start("upload")),
                          ("Sync both ways", lambda: self.start("both"))):
            b = ttk.Button(act, text=text, command=cmd)
            b.pack(side="left", padx=(8, 0))
            self.buttons.append(b)
        self.cancel_btn = ttk.Button(act, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="right")
        ttk.Button(act, text="How sync works", command=self.explain).pack(side="right", padx=(0, 8))
        self.conflicts_btn = ttk.Button(act, text="Conflicts", command=self.show_conflicts)
        self.conflicts_btn.pack(side="right", padx=(0, 8))

        auto = ttk.LabelFrame(self, text="Automatic sync (runs in the background, even when this app is closed)",
                              padding=8)
        auto.pack(fill="x", padx=10, pady=(8, 0))
        r1 = ttk.Frame(auto)
        r1.pack(fill="x")
        self.v_auto = tk.BooleanVar(value=False)
        self.v_interval = tk.StringVar(value=str(self.cfg.get("auto_minutes", 15)))
        ttk.Checkbutton(r1, text="Sync both ways every", variable=self.v_auto,
                        command=self.toggle_auto).pack(side="left")
        iv = ttk.Combobox(r1, textvariable=self.v_interval, width=5, state="readonly",
                          values=[str(m) for m in autosync.INTERVALS])
        iv.pack(side="left", padx=4)
        iv.bind("<<ComboboxSelected>>", lambda e: self.toggle_auto(changed_interval=True))
        ttk.Label(r1, text="minutes").pack(side="left")
        ttk.Button(r1, text="Open sync log", command=self.open_log).pack(side="right")
        r2 = ttk.Frame(auto)
        r2.pack(fill="x", pady=(6, 0))
        ex = self.cfg.get("exit_sync") or {}
        self.v_exit_emu = tk.BooleanVar(value=bool(ex.get("emulators")))
        self.v_exit_fe = tk.BooleanVar(value=bool(ex.get("frontend")))
        ttk.Label(r2, text="Also sync right after").pack(side="left")
        ttk.Checkbutton(r2, text="an emulator closes", variable=self.v_exit_emu,
                        command=self.toggle_exit).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(r2, text="EmulationStation (ES-DE) closes", variable=self.v_exit_fe,
                        command=self.toggle_exit).pack(side="left", padx=(10, 0))
        self.auto_status = ttk.Label(self, text="", foreground="#1f5fa8", padding=(12, 2))
        self.auto_status.pack(anchor="w")

        self.banner = tk.Label(self, text="", anchor="w", padx=12, pady=7,
                               font=("TkDefaultFont", 11, "bold"))
        self.banner.pack(fill="x", padx=10, pady=(6, 0))
        self._banner("idle", "Ready.")

        logf = ttk.Frame(self, padding=(10, 6, 10, 10))
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=10, wrap="word", state="disabled")
        self.log.tag_configure("error", foreground="#c62828")
        self.log.tag_configure("warn", foreground="#e65100")
        self.log.tag_configure("good", foreground="#2e7d32", font=("TkDefaultFont", 10, "bold"))
        self.log.tag_configure("bad", foreground="#c62828", font=("TkDefaultFont", 10, "bold"))
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---- setup helpers ------------------------------------------------------------
    def _write(self, text):
        self.q.put(("log", text))

    def _log_now(self, text):
        low = text.lower()
        tag = ()
        if "finished:" in low or "stopped early:" in low:
            tag = ("good",) if " 0 failed" in low and "stopped" not in low else ("bad",)
        elif "directory not found" in low or "retrying may help" in low:
            tag = ()                    # rclone's wording when a folder isn't in the cloud yet: harmless
        elif "error" in low or "failed" in low or "unauthenticated" in low:
            tag = ("error",)
        elif "warning" in low or "protected" in low or "skipped" in low or "cancel" in low:
            tag = ("warn",)
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    BANNER = {"idle": (None, "#555555"), "busy": ("#1f5fa8", "white"), "ok": ("#2e7d32", "white"),
              "warn": ("#e65100", "white"), "stopped": ("#616161", "white"), "info": ("#e8eef7", "#1f3f6a")}

    def _banner(self, kind, text):
        bg, fg = self.BANNER.get(kind, self.BANNER["idle"])
        self.banner.config(text=text, bg=bg or self.cget("background"), fg=fg)
        self.banner_kind = kind

    def _refresh_conflicts(self, announce=False):
        n = syncplan.conflict_count()
        self.conflicts_btn.config(text=f"Conflicts ({n})" if n else "Conflicts",
                                  state="normal" if n or syncplan.all_conflicts() else "disabled")
        if announce and n:
            self._banner("warn", f"⚠  {n} conflict(s) from an earlier sync need your choice: click Conflicts")
        return n

    def show_conflicts(self):
        ConflictsDialog(self)

    def explain(self):
        messagebox.showinfo("How sync works", (
            "The newer copy of each file wins, and nothing is ever deleted: files replaced by a download "
            "go to your backup folder, files replaced by an upload go to EmuSaveManager-overwritten in "
            "the cloud. An empty file never replaces a real save, and Save History keeps earlier versions.\n\n"
            "Close your emulators before syncing. Automatic syncs skip themselves while PCSX2, DuckStation, "
            "RetroArch, Dolphin or xemu is running.\n\n"
            "If you played the same game on both devices between syncs, neither copy is overwritten: it "
            "shows under Conflicts until you pick which one to keep (the other goes to the backups).\n\n"
            "Set this up the same way on the Steam Deck (same cloud remote and base folder) and both stay "
            "in sync through the cloud. If a background sync is running when you press a sync button, "
            "it is stopped and yours runs instead."), parent=self)

    def _toggle_conn(self, show=None):
        show = (not self.conn_open) if show is None else show
        if show and not self.conn_open:
            self.conn_frame.pack(fill="x", after=self.conn_summary)
        elif not show and self.conn_open:
            self.conn_frame.pack_forget()
        self.conn_open = show
        self.conn_btn.config(text="Hide connection settings ▴" if show else "Connection settings ▾")

    def _update_conn_summary(self, collapse_if_ready=False):
        exe, remote = self.v_rclone.get(), self.v_remote.get()
        ready = bool(exe and Path(exe).is_file() and remote)
        if ready:
            v = cs.rclone_version(exe)
            kind = dict(cs.list_remotes(self.v_conf.get())).get(remote, "")
            self.conn_label.config(text=f"Connected to {remote}" + (f" ({kind})" if kind else "") +
                                   f"  ·  folder {self.v_base.get()}  ·  rclone "
                                   f"{'.'.join(map(str, v)) if v else '?'}", foreground="#1f5fa8")
        else:
            self.conn_label.config(text="Not set up yet: fill in the connection settings below.",
                                   foreground="#c62828")
        if collapse_if_ready:
            self._toggle_conn(show=not ready)

    def _browse(self, var):
        f = filedialog.askopenfilename(parent=self)
        if f:
            var.set(f)
            self._reload_remotes()

    def _autodetect(self):
        cur = self.v_rclone.get()
        if not cur or not Path(cur).is_file() or not cs.rclone_ok(cur):
            self.v_rclone.set(cs.find_rclone(cur))
        if not self.v_conf.get() or not Path(self.v_conf.get()).is_file():
            self.v_conf.set(cs.find_config(self.v_conf.get()))
        remote = self.cfg.get("remote", "")
        own = str(cs.own_config_path())
        if (remote and remote not in dict(cs.list_remotes(self.v_conf.get()))
                and remote in dict(cs.list_remotes(own))):
            self.v_conf.set(own)                         # the saved file lost the remote: use our copy
        self._reload_remotes()
        if not self.v_rclone.get():
            self._log_now("rclone was not found. Use 'Download rclone', or install it from rclone.org "
                          "and Browse to it. (If EmuDeck is installed on this PC it is already in "
                          "Emulation/tools/rclone.)")
        elif not self.combo["values"]:
            self._log_now("No cloud remote found in the rclone config. Use 'Set up EmuDeck Cloud…' "
                          "or 'Set up OneDrive…'.")

    def _reload_remotes(self):
        info = cs.remote_info(self.v_conf.get())
        remotes = [(n, t) for n, t, _ok in info]
        self._remote_types = dict(remotes)
        current = self.v_remote.get()
        # signed-in remotes only (EmuDeck's rclone.conf has an empty template for every
        # provider); the current choice stays listed so it can be seen and changed
        usable = [(n, t, ok) for n, t, ok in info
                  if (ok or n == current) and (cs.is_emudeck_cloud(n) or t not in ("", "crypt", "union", "combine"))]
        self._remote_ready = {n: ok for n, _t, ok in info}
        names = [n for n, _t, _ok in usable]
        self.combo["values"] = names
        if self.v_remote.get() not in names or not self._remote_ready.get(self.v_remote.get(), True):
            best = cs.pick_default_remote(usable)
            if best or self.v_remote.get() not in names:
                self.v_remote.set(best)
        self._on_remote(save=False)

    def _on_remote(self, save=True):
        """Show what kind of remote is selected; EmuDeck Cloud gets its base folder set."""
        remote = self.v_remote.get()
        user = self.cfg.get("emudeck_user", "")
        base = self.v_base.get().strip("/ ")
        looks_cloud = base.startswith("cs") or base.startswith("emudeck-saves/")
        if cs.is_emudeck_cloud(remote):
            self.remote_kind.config(text=cs.EMUDECK_CLOUD_VARIANTS[remote]["label"])
            if user:
                self.v_base.set(cs.emudeck_cloud_base(remote, user))
            elif not cs.emudeck_cloud_base_ok(remote, base) and self.v_rclone.get():
                self._find_emudeck_folder(remote)
        else:
            kind = getattr(self, "_remote_types", {}).get(remote, "")
            self.remote_kind.config(text={"onedrive": "OneDrive"}.get(kind, kind))
            if looks_cloud:
                self.v_base.set(cs.DEFAULT_BASE)
        if save:
            self._save()

    def download_rclone(self):
        if not messagebox.askyesno("Download rclone", "Download the official rclone build for this system "
                                   "(from downloads.rclone.org, or rclone's GitHub releases) and keep it in\n"
                                   f"{cs.OWN_RCLONE_DIR} ?", parent=self):
            return
        self._log_now("Downloading rclone…")

        def work():
            try:
                path = cs.download_rclone(log=self._write)
                self.q.put(("rclone", str(path)))
            except Exception as ex:  # noqa
                self.q.put(("log", f"Download failed: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    def setup_remote(self):
        exe = self.v_rclone.get()
        if not exe or not Path(exe).is_file():
            messagebox.showinfo("rclone", "Find or download rclone first.", parent=self)
            return
        if not self.v_conf.get():
            self.v_conf.set(str(cs.default_rclone_conf()))
        messagebox.showinfo(
            "Set up OneDrive",
            "A terminal window will open running 'rclone config'. In it:\n\n"
            "  n  (new remote)\n  name: emudeck-onedrive\n  storage: onedrive\n"
            "  press Enter for client id/secret, choose your region,\n"
            "  answer 'y' to auto config and sign in in the browser,\n"
            "  pick 'OneDrive Personal or Business', then confirm and quit with 'q'.\n\n"
            "Then press 'Reload' here.", parent=self)
        if not cs.open_rclone_config_terminal(exe, self.v_conf.get()):
            messagebox.showinfo("rclone", "Could not open a terminal. Run this yourself:\n\n"
                                f"{exe} config --config \"{self.v_conf.get()}\"", parent=self)

    def _find_emudeck_folder(self, remote):
        """EmuDeck Cloud picked without 'Set up EmuDeck Cloud…': find the account folder."""
        runner = cs.RcloneRunner(self.v_rclone.get(), self.v_conf.get(), self._write)
        self._write(f"Finding your EmuDeck Cloud folder for {remote}…")

        def work():
            user = cs.discover_emudeck_cloud_user(runner, remote)
            if user:
                self.q.put(("emudeck", self.v_conf.get(), user, remote))
            else:
                self._write("Could not work out the EmuDeck Cloud folder. Use 'Set up EmuDeck Cloud…', "
                            "or pick your OneDrive remote.")

        self._launch(runner, work)

    def setup_emudeck_cloud(self):
        exe = self.v_rclone.get()
        if not exe or not Path(exe).is_file():
            messagebox.showinfo("rclone", "Find or download rclone first.", parent=self)
            return
        tokens = []
        signins = cs.find_emudeck_signins()
        if signins:
            ans = messagebox.askyesnocancel(
                "Set up EmuDeck Cloud",
                "Found your EmuDeck Patreon sign-in on this computer.\n\n"
                "Use it to connect to EmuDeck Cloud?\n\n"
                "Yes = use it    No = paste a token instead", parent=self)
            if ans is None:
                return
            if ans:
                tokens = signins
        if not tokens:
            token = simpledialog.askstring(
                "Set up EmuDeck Cloud",
                "Paste your full EmuDeck Cloud token (it looks like  ABCD1234|||<secret> ).\n\n"
                "Easiest instead: open the EmuDeck app on this computer and sign in to Patreon,\n"
                "then click 'Set up EmuDeck Cloud…' again - the sign-in is then found automatically.\n\n"
                "The token is sent only to cloud.emudeck.com, just as EmuDeck does, to get your\n"
                "storage keys. It is not saved.", show="•", parent=self)
            if not token or not token.strip():
                return
            try:
                cs.parse_token(token)
            except cs.EmuDeckCloudError as ex:
                messagebox.showerror("EmuDeck Cloud", str(ex), parent=self)
                return
            tokens = [token]
        conf = self.v_conf.get() or str(cs.default_rclone_conf())
        runner = cs.RcloneRunner(exe, conf, self._write)
        self._write("Contacting cloud.emudeck.com…")

        def work():
            keys, last_error, used = None, None, None
            for tok in tokens:
                try:
                    keys = cs.register_emudeck_cloud(tok)
                    used = tok
                    break
                except cs.EmuDeckCloudError as ex:
                    last_error = ex
            if keys is None:
                if len(tokens) > 1 or signins:
                    raise cs.EmuDeckCloudError(f"{last_error}\nIf you signed in to EmuDeck a while ago, "
                                               "open EmuDeck once so it refreshes the sign-in, then try again.")
                raise last_error
            hint = cs.emudeck_cloud_type(used)
            if hint:
                self._write(f"EmuDeck says this account uses {cs.EMUDECK_CLOUD_VARIANTS[hint]['label']}.")
            bases = cs.write_emudeck_cloud_remotes(conf, keys)
            self._write(f"Storage keys saved to {conf} (remotes Emudeck-cloud and Emudeck-cloud2).")
            self._write("Looking for your saves in EmuDeck Cloud…")
            remote, report = cs.detect_emudeck_cloud(runner, bases, hint or keys.get("cloud"))
            for name, state in report.items():
                self._write(f"  {cs.EMUDECK_CLOUD_VARIANTS[name]['label']}: "
                            + {"has-saves": "your saves are here", "empty": "reachable, no saves yet"}
                            .get(state, state))
            self.q.put(("emudeck", conf, keys["user"], remote or hint))

        self._launch(runner, work)

    # ---- automatic sync ----------------------------------------------------------------------
    def _refresh_auto(self):
        on, desc = autosync.schedule_status()
        self.v_auto.set(on)
        last = autosync.read_status()
        tail = ""
        if last:
            when = last.get("time", "").replace("T", " ")
            tail = f"  ·  last sync {when}: {last.get('message') or last.get('result', '')}"
        ex = self.cfg.get("exit_sync") or {}
        if ex.get("emulators") or ex.get("frontend"):
            what = " / ".join(t for t, k in (("emulator", "emulators"), ("ES-DE", "frontend")) if ex.get(k))
            state = exitwatch.watch_status() or "starting"
            desc += f"  ·  after {what} closes: {state}"
        self.auto_status.config(text=f"Automatic sync: {desc}{tail}")

    def toggle_auto(self, changed_interval=False):
        if changed_interval and not self.v_auto.get():
            self.cfg["auto_minutes"] = int(self.v_interval.get())
            self._save()
            return
        if self.v_auto.get():
            if not self._ready(background=True):
                self.v_auto.set(False)
                return
            self._save()
            minutes = int(self.v_interval.get())
            try:
                desc = autosync.enable_schedule(minutes)
            except (OSError, ValueError) as ex:
                self.v_auto.set(False)
                messagebox.showerror("Automatic sync", str(ex), parent=self)
                return
            self.cfg["auto_minutes"] = minutes
            self._save()
            self._log_now(f"Automatic sync is on: {desc}. Log: {autosync.LOG_FILE}")
        else:
            try:
                autosync.disable_schedule()
            except OSError as ex:
                messagebox.showerror("Automatic sync", str(ex), parent=self)
            self._log_now("Automatic sync is off.")
        self._refresh_auto()

    def toggle_exit(self):
        opts = {"emulators": self.v_exit_emu.get(), "frontend": self.v_exit_fe.get()}
        old = dict(self.cfg.get("exit_sync") or {})
        if any(opts.values()) and not self._ready(background=True):
            self.v_exit_emu.set(bool(old.get("emulators")))
            self.v_exit_fe.set(bool(old.get("frontend")))
            return
        self.cfg["exit_sync"] = opts
        self._save()                          # the watcher reads these settings while it runs
        try:
            if any(opts.values()):
                desc = exitwatch.enable_watch()
                self._log_now(f"Sync-on-exit is on ({desc}). Log: {autosync.LOG_FILE}")
            else:
                exitwatch.disable_watch()
                self._log_now("Sync-on-exit is off.")
        except OSError as ex:
            self.cfg["exit_sync"] = old
            self._save()
            self.v_exit_emu.set(bool(old.get("emulators")))
            self.v_exit_fe.set(bool(old.get("frontend")))
            messagebox.showerror("Sync on exit", str(ex), parent=self)
        self.after(1500, self._refresh_auto)

    def open_log(self):
        autosync.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not autosync.LOG_FILE.exists():
            autosync.LOG_FILE.write_text("", encoding="utf-8")
        self.app.open_folder(autosync.LOG_FILE)

    # ---- rows ------------------------------------------------------------------------------
    def _load_pairs(self):
        self.tree.delete(*self.tree.get_children())
        self.pairs = {}
        for emu, prof in self.app.profiles.items():
            resolved = self.app.resolve(emu)
            for loc in prof["locations"]:
                label = loc["label"]
                key = f"{emu}::{label}"
                saved = self.cfg["pairs"].get(key, {})
                local = resolved.get(label)
                self.pairs[key] = {
                    "emu": emu, "label": label, "local": local,
                    "sub": cs.effective_subpath(emu, label, saved.get("sub")),
                    "enabled": saved.get("enabled", (emu, label) not in cs.DISABLED_BY_DEFAULT),
                }
                self.tree.insert("", "end", iid=key)
                self._refresh_row(key)

    def _refresh_row(self, key):
        p = self.pairs[key]
        local = str(p["local"]) if p["local"] and Path(p["local"]).is_dir() else "(folder not found)"
        base = self.v_base.get().strip("/ ")
        self.tree.item(key, values=("✓" if p["enabled"] else "✗", p["emu"], p["label"], local,
                                    f"{base}/{p['sub']}"))

    def _toggle(self, event):
        if self._drag and self._drag.get("moved"):
            return
        key = self.tree.identify_row(event.y)
        if key and self.tree.identify_column(event.x) != "#1":   # a tap on ✓ / ✗ already switched it
            self._flip(key)

    def _flip(self, key):
        self.pairs[key]["enabled"] = not self.pairs[key]["enabled"]
        self._refresh_row(key)
        self._save()

    def _toggle_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("What to sync", "Select a row first.", parent=self)
            return "break"
        self._flip(sel[0])
        return "break"

    def _set_all(self, on):
        for key, p in self.pairs.items():
            if p["enabled"] != on:
                p["enabled"] = on
                self._refresh_row(key)
        self._save()

    # Touchscreens send a press, motion and release rather than wheel events, so dragging a finger
    # over the list scrolls it, and a tap (no drag) on the Sync column switches that row.
    ROW_DRAG = 24

    def _tree_press(self, event):
        self._drag = {"y": event.y, "moved": False, "row": self.tree.identify_row(event.y),
                      "col": self.tree.identify_column(event.x),
                      "heading": self.tree.identify_region(event.x, event.y) == "heading"}

    def _tree_drag(self, event):
        d = self._drag
        if not d or d["heading"]:
            return
        dy = event.y - d["y"]
        if abs(dy) >= self.ROW_DRAG:
            rows = int(dy / self.ROW_DRAG)
            self.tree.yview_scroll(-rows, "units")
            d["y"] += rows * self.ROW_DRAG
            d["moved"] = True
        if d["moved"]:
            return "break"                   # don't drag the selection along with the finger

    def _tree_release(self, event):
        d = self._drag
        if d and not d["moved"] and not d["heading"] and d["col"] == "#1" and d["row"] \
                and d["row"] == self.tree.identify_row(event.y):
            self._flip(d["row"])

    def _edit_remote(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Cloud folder", "Select a row first.", parent=self)
            return
        p = self.pairs[sel[0]]
        new = simpledialog.askstring("Cloud folder", f"Folder under '{self.v_base.get()}' for "
                                     f"{p['emu']} / {p['label']}:", initialvalue=p["sub"], parent=self)
        if new and new.strip("/ "):
            p["sub"] = new.strip("/ ")
            self._refresh_row(sel[0])
            self._save()

    def _save(self):
        own = cs.adopt_config(self.v_conf.get(), self.v_remote.get())   # independent of EmuDeck's file
        if own != self.v_conf.get():
            self.v_conf.set(own)
        self.cfg.update({"rclone": self.v_rclone.get(), "config": self.v_conf.get(),
                         "remote": self.v_remote.get(), "base": self.v_base.get().strip() or cs.DEFAULT_BASE})
        self.cfg["pairs"] = {k: {"sub": p["sub"], "enabled": p["enabled"]} for k, p in self.pairs.items()}
        self.app.save_cfg()
        try:
            self._update_conn_summary()
        except tk.TclError:
            pass

    # ---- running ------------------------------------------------------------------------------
    def _ready(self, background=False):
        """background: for automatic sync, which fetches a current rclone by itself."""
        exe, remote = self.v_rclone.get(), self.v_remote.get()
        if (not background and exe and Path(exe).is_file() and not cs.rclone_ok(exe)
                and cs.needs_new_rclone(self.v_conf.get(), remote)):
            better = cs.find_rclone(exe)
            if cs.rclone_ok(better):
                self.v_rclone.set(better)
                exe = better
                self._log_now(f"Using the newer rclone at {better}.")
            else:
                messagebox.showinfo("Cloud Sync", "This rclone is too old to upload to OneDrive (it needs "
                                    "1.66 or newer). Use 'Download rclone' first.", parent=self)
                return False
        if not exe or not Path(exe).is_file():
            messagebox.showinfo("Cloud Sync", "rclone was not found. Use 'Download rclone' or Browse to it.", parent=self)
            return False
        if not remote:
            messagebox.showinfo("Cloud Sync", "Choose or set up a cloud remote first "
                                "(EmuDeck Cloud or OneDrive).", parent=self)
            return False
        return True

    def _set_busy(self, busy):
        self.busy = busy
        for b in self.buttons:
            b.config(state="disabled" if busy else "normal")
        self.cancel_btn.config(state="normal" if busy else "disabled")

    def test(self):
        if self.busy or not self._ready():
            return
        self._save()
        runner = cs.RcloneRunner(self.v_rclone.get(), self.v_conf.get(), self._write)
        remote, base = self.v_remote.get(), self.v_base.get()
        self._banner("busy", "⟳  Testing the connection…")

        def work():
            if cs.test_connection(runner, remote, base):
                self._write("Connection OK.")
                self.q.put(("banner", "ok", "✓  Connection OK"))
            else:
                self._write("Connection FAILED - see the messages above.")
                self.q.put(("banner", "warn", "✗  Connection failed - see the log below"))

        self._launch(runner, work)

    def start(self, mode):
        if self.busy or not self._ready():
            return
        self._save()
        jobs = [p for p in self.pairs.values()
                if p["enabled"] and p["local"] and Path(p["local"]).is_dir()]
        if not jobs:
            messagebox.showinfo("Cloud Sync", "Nothing to sync: switch on at least one row whose "
                                "folder exists on this PC.", parent=self)
            return
        dry = self.v_dry.get()
        lock = autosync.SyncLock(kind="window")
        other = lock.busy_with()
        takeover = ""
        if other:
            if int(other.get("pid") or 0) == os.getpid():
                messagebox.showinfo("Cloud Sync", "A restore from Save History is still running. Try again "
                                    "when it's done.", parent=self)
                return
            started = other.get("started") or 0
            mins = max(0, int((time.time() - started) / 60)) if started else 0
            takeover = (f"A background sync is running right now (started {mins} min ago). It will be "
                        f"stopped first, then yours runs.\n\n")
        what = {"download": "Cloud files NEWER than yours will replace the ones on this device.",
                "upload": "Files on this device NEWER than the cloud copy will replace the cloud copy.",
                "both": "The newest copy of each file wins, in both directions."}[mode]
        if dry:
            what = "Preview only: nothing will be changed."
        if (not dry or takeover) and not messagebox.askokcancel(
                "Cloud Sync", f"{takeover}{what}\n\nClose your emulators first. Continue?", parent=self):
            return
        runner = cs.RcloneRunner(self.v_rclone.get(), self.v_conf.get(), self._write)
        remote, base = self.v_remote.get(), self.v_base.get()
        log = autosync.Log(echo=self._write)
        label = {"download": "Downloading", "upload": "Uploading", "both": "Syncing"}[mode]
        self._banner("busy", f"⟳  {label}…" + ("  (preview)" if dry else ""))

        def work():
            if takeover:
                self._write("Stopping the background sync first…")
                self.q.put(("banner", "busy", "⟳  Stopping the background sync first…"))
                if not lock.request_stop(log=self._write):
                    self._write("The background sync couldn't be stopped. Try again in a minute.")
                    self.q.put(("banner", "warn", "✗  Couldn't stop the background sync - try again in a minute"))
                    return
                self.q.put(("banner", "busy", f"⟳  {label}…" + ("  (preview)" if dry else "")))
            if not lock.acquire():
                self._write("Another sync started at the same moment - try again in a minute.")
                self.q.put(("banner", "warn", "✗  Another sync is running - try again in a minute"))
                return
            try:
                ok, failed = autosync.run_jobs(runner, jobs, remote, base, mode, dry,
                                               self.app.backup_dir, log,
                                               history=self.app.cfg.get("history"))
                tail = "Cancelled. " if runner.cancelled else ""
                msg = f"{ok} step(s) OK, {failed} failed" + (" (preview)" if dry else "")
                log(f"\n{tail}Finished: {msg}.")
                autosync.write_status(result="ok" if not failed else "failed", ok=ok, failed=failed,
                                      message=msg + " (from the window)")
                now = datetime.now().strftime("%H:%M")
                n_conf = 0 if dry else syncplan.conflict_count()
                if runner.cancelled:
                    self.q.put(("banner", "stopped", f"■  Cancelled at {now}  -  {ok} step(s) done"))
                elif failed:
                    self.q.put(("banner", "warn", f"⚠  Finished at {now} with {failed} failed step(s)  -  "
                                                  f"see the red lines in the log"))
                elif n_conf:
                    self.q.put(("banner", "warn", f"⚠  Finished at {now}  -  {n_conf} conflict(s) need your "
                                                  f"choice: click Conflicts"))
                else:
                    self.q.put(("banner", "ok", f"✓  {'Preview' if dry else 'Sync'} complete at {now}  -  "
                                                f"{ok} step(s) OK" + ("  (nothing was changed)" if dry else "")))
            finally:
                lock.release()

        self._launch(runner, work)

    def _launch(self, runner, func):
        self.runner = runner
        self._set_busy(True)

        def run():
            try:
                func()
            except Exception as ex:  # noqa
                self._write(f"Error: {ex}")
            self.q.put(("done",))

        threading.Thread(target=run, daemon=True).start()

    def cancel(self):
        if self.runner:
            self.runner.cancel()
            self._write("Cancelling…")

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self._log_now(msg[1])
                elif msg[0] == "banner":
                    self._banner(msg[1], msg[2])
                elif msg[0] == "done":
                    self._set_busy(False)
                    self.runner = None
                    self._refresh_conflicts()
                    if self.banner_kind == "busy":
                        self._banner("idle", "Done.")
                    self.app.refresh_all()
                    self._refresh_auto()
                elif msg[0] == "emudeck":
                    _tag, conf, user, remote = msg
                    self.cfg["emudeck_user"] = user
                    self.v_conf.set(conf)
                    self._reload_remotes()
                    if remote:
                        self.v_remote.set(remote)
                        self._on_remote()
                        self._log_now(f"EmuDeck Cloud is set up: using {remote}, folder {self.v_base.get()}."
                                      " Try 'Sync both ways' with 'Preview only' ticked first.")
                    else:
                        self._on_remote()
                        self._log_now("Both EmuDeck Cloud remotes were added, but it isn't clear which one "
                                      "your Deck uses. Pick Emudeck-cloud or Emudeck-cloud2 above (EmuDeck's "
                                      "CloudSync settings on the Deck show it), then 'Test connection'.")
                    self._save()
                elif msg[0] == "rclone":
                    self.v_rclone.set(msg[1])
                    self._log_now(f"rclone installed: {msg[1]}")
                    self._save()
        except queue.Empty:
            pass
        self._ticks += 1
        if self._ticks % 30 == 0 and not self.busy:          # every ~3 s: show a running background sync
            self._show_background()
        if self.winfo_exists():
            self.after(100, self._poll)

    def _show_background(self):
        try:
            other = autosync.SyncLock().busy_with()
        except Exception:  # noqa
            other = None
        if other and other.get("kind") != "restore" and int(other.get("pid") or 0) != os.getpid():
            started = datetime.fromtimestamp(other.get("started") or time.time()).strftime("%H:%M")
            self._banner("info", f"⟳  A background sync is running (started {started}). Pressing a sync "
                                 f"button stops it and runs yours.")
            self._bg_shown = True
        elif getattr(self, "_bg_shown", False):
            self._bg_shown = False
            self._refresh_auto()
            last = autosync.read_status()
            if last.get("result") == "ok":
                self._banner("ok", f"✓  Background sync complete  -  {last.get('message', '')}")
            elif last.get("result") in ("failed",):
                self._banner("warn", f"⚠  Background sync finished with problems  -  {last.get('message', '')}")
            else:
                self._banner("idle", "Ready.")

    def _close(self):
        if self.busy and not messagebox.askyesno("Cloud Sync", "A sync is still running. Cancel it and close?",
                                                 parent=self):
            return
        self.cancel()
        self._save()
        self.destroy()


def _save_title(local_dir, item):
    """The game's name from a PS2 save's icon.sys, if this item is a folder-card save."""
    import unicodedata
    try:
        b = (Path(local_dir) / item / "icon.sys").read_bytes()
        if b[:4] == b"PS2D":
            t = b[0xC0:0xC0 + 68].split(b"\0")[0].decode("shift_jis", "replace")
            return unicodedata.normalize("NFKC", t).replace("\u3000", " ").strip()
    except OSError:
        pass
    return ""


class ConflictsDialog(tk.Toplevel):
    """Saves changed on both devices since the last sync: pick which copy to keep."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Conflicts - saves changed on both devices")
        self.geometry(f"{min(900, self.winfo_screenwidth() - 40)}x{min(460, self.winfo_screenheight() - 120)}")
        self.transient(parent)
        ttk.Label(self, wraplength=860, padding=(12, 10, 12, 4), text=(
            "These were changed on this device AND in the cloud since the last sync, so neither was "
            "overwritten. Pick the copy to keep for each; the other copy is moved to the backup folder "
            "(nothing is lost). Your choice is applied at the next sync.")).pack(anchor="w")
        cols = ("where", "what", "here", "cloud", "choice")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="extended")
        for c, text, w in (("where", "Folder", 160), ("what", "Save", 230), ("here", "This device", 180),
                           ("cloud", "Cloud", 180), ("choice", "Keep", 100)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12)
        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        ttk.Button(bar, text="Keep this device's", command=lambda: self._choose("local")).pack(side="left")
        ttk.Button(bar, text="Keep the cloud's", command=lambda: self._choose("cloud")).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=self._close).pack(side="right")
        self.sync_btn = ttk.Button(bar, text="Apply now (Sync both ways)", command=self._apply)
        self.sync_btn.pack(side="right", padx=6)
        self.rows = {}
        self._load()

    @staticmethod
    def _desc(t, size):
        return f"{datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M') if t else '?'}  ·  {size // 1024 or 1} KB"

    def _load(self):
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        namers = {}
        for i, (f, st, item, info) in enumerate(syncplan.all_conflicts()):
            title = _save_title(st.get("local", ""), item) or self._game(namers, f, st, item)
            what = f"{title}  ({item.split('/')[-1]})" if title else item
            choice = {"local": "this device's", "cloud": "the cloud's"}.get(st.get("resolve", {}).get(item), "?")
            iid = f"c{i}"
            self.rows[iid] = (f, item)
            self.tree.insert("", "end", iid=iid, values=(
                f"{st.get('emu', '')} / {st.get('label', '')}", what,
                self._desc(info.get("local_time"), info.get("local_size", 0)),
                self._desc(info.get("cloud_time"), info.get("cloud_size", 0)), choice))

    def _game(self, namers, f, st, item):
        """The game's name for any emulator's save (from the same reading as the save list)."""
        import savenames
        if f not in namers:
            local = st.get("local", "")
            rels = list(st.get("conflicts", {}))
            try:
                rels += [r for r in syncplan.local_listing(Path(local))] if local and Path(local).is_dir() else []
            except Exception:  # noqa
                pass
            titles = getattr(getattr(self.parent, "app", None), "titles", None) or savenames.cached_titles()
            namers[f] = savenames.Namer(st.get("emu", ""), st.get("label", ""), local or None, rels, titles)
        return namers[f].name(item)

    def _choose(self, side):
        sel = self.tree.selection() or self.tree.get_children()[:1]
        for iid in sel:
            f, item = self.rows[iid]
            syncplan.choose(f, item, side)
        self._load()
        self.parent._refresh_conflicts()

    def _apply(self):
        self._close()
        self.parent.start("both")

    def _close(self):
        self.parent._refresh_conflicts()
        self.destroy()
