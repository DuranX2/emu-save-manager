"""ui_cloud.py - window for syncing saves with OneDrive in EmuDeck's folder layout."""

import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import cloudsync as cs


class CloudSyncDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Cloud Sync (OneDrive, EmuDeck layout)")
        self.geometry("980x700")
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

        ttk.Label(top, text="OneDrive remote:").grid(row=2, column=0, sticky="w", pady=2)
        self.combo = ttk.Combobox(top, textvariable=self.v_remote, width=30)
        self.combo.grid(row=2, column=1, sticky="w", padx=6)
        bar2 = ttk.Frame(top)
        bar2.grid(row=2, column=2, sticky="e")
        ttk.Button(bar2, text="Reload", command=self._reload_remotes).pack(side="left")
        ttk.Button(bar2, text="Set up OneDrive…", command=self.setup_remote).pack(side="left", padx=4)

        ttk.Label(top, text="Cloud base folder:").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self.v_base, width=30).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(top, text="EmuDeck uses EmuDeck/saves", foreground="#666").grid(row=3, column=2, sticky="e")

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

        note = ("Newer file wins and nothing is ever deleted. Files replaced by a download are moved to "
                "your backup folder first. Close your emulators before syncing, and avoid editing the "
                "same memory card on two devices between syncs.")
        ttk.Label(self, text=note, wraplength=930, foreground="#555", padding=(10, 0)).pack(anchor="w")

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
            self._log_now("No OneDrive remote found in the rclone config. Use 'Set up OneDrive…', or "
                          "set up CloudSync in EmuDeck first and point 'rclone config' at its rclone.conf.")

    def _reload_remotes(self):
        remotes = cs.list_remotes(self.v_conf.get())
        names = [n for n, t in remotes if t == "onedrive"]
        self.combo["values"] = names
        if self.v_remote.get() not in names:
            self.v_remote.set(cs.pick_default_remote(remotes))

    def download_rclone(self):
        if not messagebox.askyesno("Download rclone", "Download the official rclone build for this system "
                                   "from downloads.rclone.org and keep it in\n"
                                   f"{cs.OWN_RCLONE_DIR} ?", parent=self):
            return
        self._log_now("Downloading rclone…")

        def work():
            try:
                path = cs.download_rclone()
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
                    "sub": saved.get("sub") or cs.default_subpath(emu, label),
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
            messagebox.showinfo("Cloud Sync", "Choose or set up a OneDrive remote first.", parent=self)
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
        remote = self.v_remote.get()
        self._launch(runner, lambda: self._write("Connection OK." if cs.test_connection(runner, remote)
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
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_root = self.app.backup_dir / "cloud_overwritten" / stamp

        def work():
            ok = failed = 0
            for p in jobs:
                if runner.cancelled:
                    break
                rpath = cs.remote_path(remote, base, p["sub"])
                self._write(f"\n== {p['emu']} / {p['label']} ==")
                steps = ["download", "upload"] if mode == "both" else [mode]
                for step in steps:
                    if runner.cancelled:
                        break
                    self._write(("Downloading" if step == "download" else "Uploading") + f"  {rpath}")
                    bk = backup_root / p["emu"] / p["label"] if step == "download" else None
                    res = cs.sync_pair(runner, step, Path(p["local"]), rpath, dry, bk)
                    if res == "nothing-in-cloud":
                        self._write("Nothing in the cloud for this folder yet.")
                    elif res == "failed":
                        failed += 1
                    else:
                        ok += 1
            tail = "Cancelled. " if runner.cancelled else ""
            self._write(f"\n{tail}Finished{' (preview)' if dry else ''}: {ok} step(s) OK, {failed} failed.")

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
