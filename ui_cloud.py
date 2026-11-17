"""ui_cloud.py - window for syncing saves with OneDrive or EmuDeck Cloud in EmuDeck's folder layout."""

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import autosync
import exitwatch
import cloudsync as cs


class CloudSyncDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Cloud Sync (OneDrive or EmuDeck Cloud, EmuDeck layout)")
        self.geometry("980x780")
        self.minsize(820, 560)
        self.transient(app)
        self.cfg = app.cfg.setdefault("cloud", {})
        self.cfg.setdefault("pairs", {})
        self.q = queue.Queue()
        self.runner = None
        self.busy = False
        self.pairs = {}                       # iid -> dict
        self._build()
        self._autodetect()
        self._load_pairs()
        self._refresh_auto()
        self.v_base.trace_add("write", lambda *_: [self._refresh_row(k) for k in self.pairs])
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)

    # ---- UI -------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
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

        frame = ttk.LabelFrame(self, text="What to sync (double-click a row to switch it on or off)", padding=6)
        frame.pack(fill="x", padx=10)
        cols = ("sync", "emu", "loc", "local", "cloud")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=7, selectmode="browse")
        for c, text, w in (("sync", "Sync", 50), ("emu", "Emulator", 100), ("loc", "Location", 110),
                           ("local", "This PC", 340), ("cloud", "Cloud folder", 260)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="center" if c == "sync" else "w")
        self.tree.pack(fill="x")
        self.tree.bind("<Double-1>", self._toggle)
        ttk.Button(frame, text="Edit cloud folder…", command=self._edit_remote).pack(anchor="w", pady=(6, 0))

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

        note = ("Newer file wins and nothing is ever deleted: files replaced by a download go to your "
                "backup folder, files replaced by an upload go to EmuSaveManager-overwritten in the cloud. "
                "Close your emulators before syncing, and avoid editing the same memory card file on two "
                "devices between syncs (PCSX2 folder memory cards keep each game separate).")
        ttk.Label(self, text=note, wraplength=930, foreground="#555", padding=(10, 0)).pack(anchor="w")

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
        ttk.Label(self, text="Skipped automatically while PCSX2, DuckStation, RetroArch, Dolphin or xemu is "
                             "running. Set this up the same way on the Steam Deck (same cloud remote and "
                             "base folder) and both stay in sync through the cloud.",
                  wraplength=930, foreground="#555", padding=(10, 2)).pack(anchor="w")

        logf = ttk.Frame(self, padding=10)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled")
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---- setup helpers ------------------------------------------------------------
    def _write(self, text):
        self.q.put(("log", text))

    def _log_now(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _browse(self, var):
        f = filedialog.askopenfilename(parent=self)
        if f:
            var.set(f)
            self._reload_remotes()

    def _autodetect(self):
        if not self.v_rclone.get() or not Path(self.v_rclone.get()).is_file():
            self.v_rclone.set(cs.find_rclone(self.v_rclone.get()))
        if not self.v_conf.get() or not Path(self.v_conf.get()).is_file():
            self.v_conf.set(cs.find_config(self.v_conf.get()))
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
            if not self._ready():
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
        if any(opts.values()) and not self._ready():
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
        key = self.tree.identify_row(event.y)
        if key:
            self.pairs[key]["enabled"] = not self.pairs[key]["enabled"]
            self._refresh_row(key)
            self._save()

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
        self.cfg.update({"rclone": self.v_rclone.get(), "config": self.v_conf.get(),
                         "remote": self.v_remote.get(), "base": self.v_base.get().strip() or cs.DEFAULT_BASE})
        self.cfg["pairs"] = {k: {"sub": p["sub"], "enabled": p["enabled"]} for k, p in self.pairs.items()}
        self.app.save_cfg()

    # ---- running ------------------------------------------------------------------------------
    def _ready(self):
        exe, remote = self.v_rclone.get(), self.v_remote.get()
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
        self._launch(runner, lambda: self._write("Connection OK." if cs.test_connection(runner, remote, base)
                                                 else "Connection FAILED - see the messages above."))

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
        if not dry:
            what = {"download": "Cloud files NEWER than yours will replace the ones on this PC.",
                    "upload": "Files on this PC NEWER than the cloud copy will replace the cloud copy.",
                    "both": "The newest copy of each file wins, in both directions."}[mode]
            if not messagebox.askokcancel("Cloud Sync", f"{what}\n\nClose your emulators first. Continue?",
                                          parent=self):
                return
        runner = cs.RcloneRunner(self.v_rclone.get(), self.v_conf.get(), self._write)
        remote, base = self.v_remote.get(), self.v_base.get()
        log = autosync.Log(echo=self._write)

        def work():
            lock = autosync.SyncLock()
            if not lock.acquire():
                self._write("A background sync is running right now - try again in a minute.")
                return
            try:
                ok, failed = autosync.run_jobs(runner, jobs, remote, base, mode, dry,
                                               self.app.backup_dir, log)
                tail = "Cancelled. " if runner.cancelled else ""
                msg = f"{ok} step(s) OK, {failed} failed" + (" (preview)" if dry else "")
                log(f"\n{tail}Finished: {msg}.")
                autosync.write_status(result="ok" if not failed else "failed", ok=ok, failed=failed,
                                      message=msg + " (from the window)")
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
                elif msg[0] == "done":
                    self._set_busy(False)
                    self.runner = None
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
        if self.winfo_exists():
            self.after(100, self._poll)

    def _close(self):
        if self.busy and not messagebox.askyesno("Cloud Sync", "A sync is still running. Cancel it and close?",
                                                 parent=self):
            return
        self.cancel()
        self._save()
        self.destroy()
