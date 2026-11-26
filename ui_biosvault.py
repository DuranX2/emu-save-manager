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
               "in-set": "Folder set",
               "unknown": "Unknown", "damaged": "Damaged"}


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
        for tag, color in (("damaged", self.c["bad"]), ("unknown", self.c["warn"]), ("system", "")):
            self.tree.tag_configure(tag, foreground=color or None)
        self.tree.tag_configure("system", font=("TkDefaultFont", 10, "bold"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        right = ttk.LabelFrame(body, text="This device", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(0, 6))
        self.install_btn = ttk.Button(bar, text="Install to this device", command=self.install_now)
        self.install_btn.pack(side="left")
        ttk.Button(bar, text="Preview", command=lambda: self.install_now(dry=True)).pack(side="left", padx=4)
        ttk.Button(bar, text="Remove what the app installed", command=self.uninstall).pack(side="right")
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
                             "except your own cloud drive (folder \"Emulator BIOS Vault\"). Files are "
                             "identified by content; unknown or damaged files are kept but never installed. "
                             "Nothing in an emulator folder is overwritten.",
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
            bad = sum(1 for e in items if e.ident.status in ("damaged", "unknown"))
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
    def change_vault(self):
        d = filedialog.askdirectory(parent=self, title="Choose the vault folder",
                                    initialdir=str(self.vault))
        if d:
            self.vcfg["folder"] = d
            self.app.save_cfg()
            self.refresh()

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
        self._run(lambda: biostargets.install_all(self.entries, self.app.resolve, self._write, dry))

    def uninstall(self):
        if not messagebox.askyesno(
                "Remove installed BIOS files",
                "Move every BIOS file the app installed on this device (and that is unchanged) "
                "to the backup folder?\n\nFiles you put there yourself are not touched.", parent=self):
            return
        self._run(lambda: biostargets.uninstall(self.app.backup_dir, self._write))
