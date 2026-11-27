"""ui_biosvault.py - BIOS Vault window: your own BIOS files, identified, synced and
installed into every emulator on this device.  Left: the vault.  Right: this device."""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import biosdb
import biostargets
import biosvault

STATUS_TEXT = {"verified": "Verified", "identified": "Identified", "side-file": "PCSX2 file",
               "in-set": "Folder set", "unverified": "Unverified", "fixable": "Fixable",
               "packed": "Zip of BIOS",
               "unknown": "Unknown", "damaged": "Damaged"}
TO_CHECK = ("damaged", "unknown", "fixable", "packed")


class BiosVaultDialog(tk.Toplevel):
    def __init__(self, app, master=None):
        """app gives the settings and emulator folders (the Save Manager's, or SaveManagerLink in
        the ROM Library); master is the window it belongs to (app when not given)."""
        master = master or app
        super().__init__(master)
        self.app = app
        self.title("BIOS Vault")
        self.geometry("1220x720")
        self.minsize(980, 560)
        self.transient(master)
        import apptheme
        self.c = apptheme.colors(master)
        self.q = queue.Queue()
        self.busy = False
        self.entries = []
        self.targets = []
        self._build()
        self.after(100, self._poll)
        self.refresh()

    # ---- settings ------------------------------------------------------------------
    @property
    def vcfg(self) -> dict:
        return self.app.cfg.setdefault("bios_vault", {})

    @property
    def vault(self) -> Path:
        return biosvault.vault_dir(self.app.cfg)

    # ---- layout ------------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Vault folder:").pack(side="left")
        self.vault_label = ttk.Label(top, text=str(self.vault))
        self.vault_label.pack(side="left", padx=6)
        ttk.Button(top, text="Change…", command=self.change_vault).pack(side="left")
        ttk.Button(top, text="Open", command=lambda: self.app.open_folder(self.vault)).pack(side="left", padx=4)
        self.sync_btn = ttk.Button(top, text="Sync vault with cloud", command=self.sync_now)
        self.sync_btn.pack(side="right")
        ttk.Button(top, text="Change…", command=self.change_cloud_folder).pack(side="right", padx=(4, 10))
        self.cloud_label = ttk.Label(top, text="")
        self.cloud_label.pack(side="right")
        self._show_cloud()

        body = ttk.Frame(self, padding=(10, 4))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3, uniform="p")
        body.columnconfigure(1, weight=2, uniform="p")
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Your vault", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Add files…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="Add folder…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(bar, text="Move to system…", command=self.move_selected).pack(side="left")
        ttk.Button(bar, text="Organise…", command=self.organise).pack(side="left", padx=4)
        self.vault_info = ttk.Label(bar, text="", foreground=self.c["dim"])
        self.vault_info.pack(side="right")
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("what", "status", "size"), show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="System / file")
        self.tree.heading("what", text="Identified as")
        self.tree.heading("status", text="Status")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=300)
        self.tree.column("#0", width=280)
        self.tree.column("what", width=230)
        self.tree.column("status", width=86, stretch=False)
        self.tree.column("size", width=82, anchor="e", stretch=False)
        for tag, color in (("damaged", self.c["bad"]), ("unknown", self.c["warn"]), ("fixable", self.c["warn"]),
                           ("packed", self.c["warn"]), ("unverified", self.c["dim"]), ("system", "")):
            self.tree.tag_configure(tag, foreground=color or None)
        self.tree.tag_configure("system", font=("TkDefaultFont", 10, "bold"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.why = ttk.Label(left, text="Select a file to see what it is, or why it isn't recognised.",
                             foreground=self.c["dim"], wraplength=620, justify="left")
        self.why.pack(fill="x", pady=(6, 0))
        self.tree.bind("<<TreeviewSelect>>", self._explain)

        right = ttk.LabelFrame(body, text="This device", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(0, 6))
        self.install_btn = ttk.Button(bar, text="Install to this device", command=self.install_now)
        self.install_btn.pack(side="left")
        self.preview_btn = ttk.Button(bar, text="Preview", command=lambda: self.install_now(dry=True))
        self.preview_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Remove installed", command=self.uninstall).pack(side="right")
        self.report_btn = ttk.Button(bar, text="Open report", command=self.open_report)
        if (biosvault.APP_DIR / "logs" / biostargets.REPORT_NAME).exists():
            self.report_btn.pack(side="left")
        tf = ttk.Frame(right)
        tf.pack(fill="both", expand=True)
        self.ttree = ttk.Treeview(tf, columns=("state",), show="tree headings", selectmode="browse")
        self.ttree.heading("#0", text="Emulator / BIOS folder")
        self.ttree.heading("state", text="In place")
        self.ttree.column("#0", width=330)
        self.ttree.column("state", width=110, stretch=False)
        self.ttree.tag_configure("note", foreground=self.c["warn"])
        self.ttree.tag_configure("emu", font=("TkDefaultFont", 10, "bold"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.ttree.yview)
        self.ttree.configure(yscrollcommand=sb.set)
        self.ttree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        opts = ttk.Frame(self, padding=(10, 2))
        opts.pack(fill="x")
        self.v_sync = tk.BooleanVar(value=bool(self.vcfg.get("sync", False)))
        self.v_auto = tk.BooleanVar(value=bool(self.vcfg.get("auto_install", False)))
        ttk.Checkbutton(opts, text="Sync the vault with the cloud during Cloud Sync and automatic sync",
                        variable=self.v_sync, command=self._save_opts).pack(side="left")
        ttk.Checkbutton(opts, text="Install new BIOS files into emulators automatically",
                        variable=self.v_auto, command=self._save_opts).pack(side="left", padx=16)
        ttk.Label(self, text="Only your own files: the vault never downloads BIOS files from anywhere "
                             "except your own cloud drive (the cloud folder above). Files are "
                             "identified by content; unknown or damaged files are kept but never installed. "
                             "Nothing in an emulator folder is overwritten. BIOS sets MAME looks for beside the games "
                             "(Game.com's gamecom.zip) are installed into the games folders.",
                  wraplength=1180, foreground=self.c["dim"], padding=(10, 2)).pack(anchor="w")

        self.log = tk.Text(self, height=8, wrap="word", state="disabled", relief="flat",
                           background=self.c["log"], foreground=self.c["log_fg"])
        self.log.pack(fill="x", padx=10, pady=(4, 10))

    # ---- helpers -----------------------------------------------------------------------
    def _write(self, text):
        self.q.put(("log", text))

    def _log_now(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _save_opts(self):
        self.vcfg["sync"] = self.v_sync.get()
        self.vcfg["auto_install"] = self.v_auto.get()
        self.app.save_cfg()

    def _run(self, func, then_refresh=True):
        if self.busy:
            return
        self.busy = True
        self.configure(cursor="watch")

        def work():
            try:
                func()
            except Exception as ex:  # noqa - shown in the log
                self._write(f"Error: {ex}")
            self.q.put(("done", then_refresh))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self._log_now(msg[1])
                elif msg[0] == "scanned":
                    self.entries, self.targets, stats = msg[1], msg[2], msg[3]
                    self._fill(stats)
                elif msg[0] == "call":
                    msg[1]()
                elif msg[0] == "done":
                    self.busy = False
                    self.configure(cursor="")
                    if msg[1]:
                        self.refresh()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    # ---- reading ---------------------------------------------------------------------------
    def refresh(self):
        self.vault_label.config(text=str(self.vault))

        def work():
            entries = biosvault.scan(self.vault)
            targets = biostargets.find_targets(self.app.resolve)
            stats = [(t, biostargets.status_of(t, entries), biostargets.coverage(t, entries))
                     for t in targets]
            self.q.put(("scanned", entries, targets, stats))

        self._run(work, then_refresh=False)

    def _fill(self, stats):
        self.tree.delete(*self.tree.get_children())
        groups = {}
        for e in self.entries:
            groups.setdefault(e.system, []).append(e)
        for system in sorted(groups, key=lambda s: (s == biosdb.UNKNOWN, s.lower())):
            items = groups[system]
            bad = sum(1 for e in items if e.ident.status in TO_CHECK)
            node = self.tree.insert("", "end", text=f"{system}  ({len(items)})", open=system in (
                biosdb.PS2, biosdb.PS1, biosdb.XBOX, biosdb.GC, biosdb.UNKNOWN),
                values=("", f"{bad} to check" if bad else "", ""), tags=("system",))
            for e in items:
                kb = f"{max(1, e.ident.size // 1024):,} KB"
                self.tree.insert(node, "end", iid=e.rel, text=e.sub, values=(
                    e.ident.label, STATUS_TEXT.get(e.ident.status, e.ident.status), kb),
                    tags=(e.ident.status,))
        n = len(self.entries)
        ok = sum(1 for e in self.entries if e.ident.status in ("verified", "identified"))
        self.vault_info.config(text=f"{n} files · {ok} identified · {len(groups)} systems")

        self.ttree.delete(*self.ttree.get_children())
        if not stats:
            self.ttree.insert("", "end", text="No emulator BIOS folders found on this device",
                              tags=("note",))
        for t, st, notes in stats:
            total = st["present"] + st["missing"] + st["conflict"]
            node = self.ttree.insert("", "end", text=t.name, open=True,
                                     values=(f"{st['present']} of {total}",), tags=("emu",))
            self.ttree.insert(node, "end", text=str(t.folder), values=("",))
            for n_ in notes:
                self.ttree.insert(node, "end", text="⚠ " + n_, values=("",), tags=("note",))
            if st["missing"]:
                self.ttree.insert(node, "end", text=f"{st['missing']} file(s) can be installed",
                                  values=("",))

    # ---- actions ---------------------------------------------------------------------------
    def _show_cloud(self):
        self.cloud_label.config(text=f"Cloud folder: {biosvault.cloud_folder(self.app.cfg)}")

    def change_vault(self):
        d = filedialog.askdirectory(parent=self, title="Choose the vault folder",
                                    initialdir=str(self.vault))
        if d:
            self.vcfg["folder"] = d
            same = biosvault.cloud_folder_for(Path(d))
            if same and same != biosvault.cloud_folder(self.app.cfg) and messagebox.askyesno(
                    "Cloud folder",
                    f"This folder is in your cloud drive's folder on this computer.\n\n"
                    f"Sync the vault with \"{same}\" on the cloud drive (the same folder), so other "
                    f"devices use this one copy? (Otherwise it's also copied to "
                    f"\"{biosvault.CLOUD_FOLDER}\".)", parent=self):
                self.vcfg["cloud_folder"] = same
            self.app.save_cfg()
            self._show_cloud()
            self.refresh()

    def change_cloud_folder(self):
        from tkinter import simpledialog
        f = simpledialog.askstring(
            "Cloud folder",
            "The vault's folder on your cloud drive (the same on every device), for example\n"
            f"Emudeck/BIOS. Leave it empty for \"{biosvault.CLOUD_FOLDER}\".",
            initialvalue=biosvault.cloud_folder(self.app.cfg), parent=self)
        if f is None:
            return
        self.vcfg["cloud_folder"] = f.strip().strip("/\\")
        self.app.save_cfg()
        self._show_cloud()

    def _add(self, paths):
        if paths:
            self._run(lambda: biosvault.add(self.vault, paths, self._write))

    def add_files(self):
        files = filedialog.askopenfilenames(parent=self, title="Add BIOS files (zip archives are read too)")
        self._add(list(files))

    def add_folder(self):
        d = filedialog.askdirectory(parent=self, title="Add every BIOS file in a folder")
        self._add([d] if d else [])

    def move_selected(self):
        sel = [i for i in self.tree.selection() if "/" in i]
        if not sel:
            messagebox.showinfo("BIOS Vault", "Select one or more files first.", parent=self)
            return
        systems = sorted({v[0] for v in biosdb.DB.values()} | {biosdb.PS2, biosdb.XBOX, biosdb.GC})
        win = tk.Toplevel(self)
        win.title("Move to system")
        win.transient(self)
        ttk.Label(win, text=f"Move {len(sel)} file(s) into the folder of:", padding=10).pack(anchor="w")
        v = tk.StringVar(value=systems[0])
        ttk.Combobox(win, textvariable=v, values=systems, width=48).pack(padx=10)

        def ok():
            for rel in sel:
                new = biosvault.move_to_system(self.vault, rel, v.get())
                self._log_now(f"Moved {rel} -> {new}")
            win.destroy()
            self.refresh()

        ttk.Button(win, text="Move", command=ok).pack(pady=10)

    def _explain(self, _e=None):
        sel = [i for i in self.tree.selection() if "/" in i]
        e = next((x for x in self.entries if sel and x.rel == sel[0]), None)
        if e is None:
            return
        st = STATUS_TEXT.get(e.ident.status, e.ident.status)
        names = f"  Emulators look for it as: {', '.join(e.ident.names)}." if e.ident.names else ""
        self.why.config(text=f"{e.rel}\n{st}: {e.ident.label}.{names}")

    def open_report(self):
        self.app.open_folder(biosvault.APP_DIR / "logs" / biostargets.REPORT_NAME)

    def organise(self):
        if self.busy:
            return
        steps = biosvault.organise_plan(self.vault, self.entries)
        OrganiseDialog(self, steps)

    def run_organise(self, steps, move):
        from datetime import datetime
        dest = (Path(self.app.backup_dir) / "bios_vault_organised" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")) \
            if move else None
        self._run(lambda: biosvault.organise(self.vault, steps, dest, self._write))

    def sync_now(self):
        self._run(lambda: biosvault.sync_cloud(self.app.cfg, self._write)
                  and self._write("Vault synced."))

    def install_now(self, dry=False):
        if not dry and not messagebox.askokcancel(
                "Install BIOS files",
                "Copy BIOS files from the vault into the emulator folders listed on the right?\n\n"
                "Only missing files are copied; nothing that is already there is replaced.",
                parent=self):
            return
        def work():
            res = biostargets.install_all(self.entries, self.app.resolve, self._write, dry,
                                          report_dir=biosvault.APP_DIR / "logs")
            if res.get("report"):
                self._write(f"Report saved: {res['report']}  (Open report)")
                self.q.put(("call", lambda: self.report_btn.pack(side="left", after=self.preview_btn)))
        self._run(work)

    def uninstall(self):
        if not messagebox.askyesno(
                "Remove installed BIOS files",
                "Move every BIOS file the app installed on this device (and that is unchanged) "
                "to the backup folder?\n\nFiles you put there yourself are not touched.", parent=self):
            return
        self._run(lambda: biostargets.uninstall(self.app.backup_dir, self._write))


class OrganiseDialog(tk.Toplevel):
    """What Organise will do, before it does it."""
    KIND = {"copy": "Copy to", "unpack": "Unpack to", "fix": "Clean copy to", "dup": "Duplicate", "keep": "Leave"}

    def __init__(self, owner, steps):
        super().__init__(owner)
        self.owner, self.steps = owner, steps
        self.title("Organise the vault")
        self.transient(owner)
        c = owner.c
        work = [x for x in steps if x.kind in ("copy", "unpack", "fix")]
        dups = [x for x in steps if x.kind == "dup"]
        left = [x for x in steps if x.kind == "keep" and x.src.ident.status in ("unknown", "damaged")]
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{len(work)} file(s) get a tidy place (<System>/<the name emulators look for>), "
                          f"{len(dups)} are duplicates or replaced by a clean copy, {len(left)} unknown or "
                          f"damaged file(s) stay where they are.", wraplength=760).pack(anchor="w")
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=8)
        t = ttk.Treeview(tf, columns=("what", "to"), show="headings", height=16)
        t.heading("what", text="File")
        t.heading("to", text="What happens")
        t.column("what", width=400)
        t.column("to", width=400)
        sb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for x in work + dups:
            what = f"{self.KIND[x.kind]} {x.dest}" if x.dest else self.KIND[x.kind] + (f": {x.note}" if x.note else "")
            if x.dest and x.note:
                what += f"  ({x.note})"
            t.insert("", "end", values=(x.src.rel, what))
        self.v_move = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, variable=self.v_move,
                        text="Move the files they came from (and the duplicates) out of the vault, into the "
                             "backup folder. Other devices move their copies away too when they sync.").pack(anchor="w")
        ttk.Label(f, text="Nothing is deleted. Untick to only add the tidy copies.",
                  foreground=c["dim"]).pack(anchor="w")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="right")
        self.ok_btn = ttk.Button(b, text="Organise", command=self.ok)
        self.ok_btn.pack(side="right", padx=6)
        if not work and not dups:
            self.ok_btn.state(["disabled"])
        self.bind("<Escape>", lambda e: self.destroy())

    def ok(self):
        steps, move = self.steps, self.v_move.get()
        self.destroy()
        self.owner.run_organise(steps, move)
