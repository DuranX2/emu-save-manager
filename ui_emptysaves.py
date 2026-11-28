"""ui_emptysaves.py - list empty (0-byte) save files and put a kept copy back."""

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path, PurePosixPath
from tkinter import messagebox, ttk

import emptysaves as es
import savehistory as sh
import savenames

TITLE = "Empty saves"


def _human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _when(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def game_name(app, f: es.Finding) -> str:
    """The game for an empty file: read from a kept copy (the empty file itself says nothing)."""
    titles = getattr(app, "titles", None)
    for c in f.copies:
        if c.kind == "overwritten" and c.path:
            root = Path(c.path)
            for _ in PurePosixPath(f.rel).parts:
                root = root.parent
            n = savenames.Namer(f.emu, f.label, root, [f.rel], titles).name(f.rel)
            if n:
                return n
    try:
        return savenames.Namer(f.emu, f.label, f.folder, [f.rel], titles).name(f.rel)
    except Exception:  # noqa
        return ""


class EmptySavesDialog(tk.Toplevel):
    def __init__(self, app, locations=None):
        super().__init__(app)
        self.app = app
        self.title(f"{TITLE} - saves with no data, and copies to put back")
        self.geometry("1060x640")
        self.minsize(820, 480)
        self.transient(app)
        self.locations = locations
        self.findings = []
        self.rows = {}                    # iid -> Finding
        self.copy_rows = {}               # iid -> Copy
        self._build()
        self.after(50, self.scan)

    def _build(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.summary = ttk.Label(top, text="Searching your save folders…", wraplength=760, justify="left")
        self.summary.pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="Search again", command=self.scan).pack(side="right")
        self.b_all = ttk.Button(top, text="Restore all with a copy", command=self.restore_all, state="disabled")
        self.b_all.pack(side="right", padx=6)

        lf = ttk.LabelFrame(self, text="Empty save files (0 bytes)", padding=6)
        lf.pack(fill="both", expand=True, padx=10)
        cols = ("game", "file", "where", "emptied", "copy")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        for c, text, w in (("game", "Game", 220), ("file", "File", 220), ("where", "Emulator · folder", 170),
                           ("emptied", "Emptied", 120), ("copy", "Copy to put back", 260)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("copy", foreground="#1f7a1f")
        self.tree.tag_configure("none", foreground="#888")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_copies())

        cf = ttk.LabelFrame(self, text="Copies of the selected file (newest first)", padding=6)
        cf.pack(fill="x", padx=10, pady=(8, 0))
        self.copies = ttk.Treeview(cf, columns=("from", "size"), show="headings", selectmode="browse", height=4)
        self.copies.heading("from", text="Kept")
        self.copies.heading("size", text="Size")
        self.copies.column("from", width=520, anchor="w")
        self.copies.column("size", width=100, anchor="e")
        self.copies.pack(side="left", fill="x", expand=True)
        bf = ttk.Frame(cf)
        bf.pack(side="left", padx=(8, 0))
        self.b_one = ttk.Button(bf, text="Restore this copy", command=self.restore_selected_copy, state="disabled")
        self.b_one.pack(fill="x")
        ttk.Button(bf, text="Open folder", command=self.open_folder).pack(fill="x", pady=(6, 0))

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        ttk.Label(bottom, foreground="#666", wraplength=900, justify="left",
                  text="Restoring puts the copy back with today's date, so the next sync sends it to your "
                       "other devices. The empty file is kept in the backup folder (empty_saves), never "
                       "deleted. Some emulators keep empty files on purpose: restore only saves you "
                       "recognise.").pack(side="left")
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="right")

    # ---- searching ------------------------------------------------------------------
    def scan(self):
        self.summary.config(text="Searching your save folders…")
        self.tree.delete(*self.tree.get_children())
        self.copies.delete(*self.copies.get_children())
        self.b_all.config(state="disabled")
        locs = self.locations if self.locations is not None else es.locations_of(self.app)
        backup = Path(self.app.backup_dir)
        settings = self.app.cfg.get("history", {})

        result = []

        def work():
            try:
                found = es.scan(locs, backup, settings)
                names = {id(f): game_name(self.app, f) for f in found}
                result.append((found, names, ""))
            except Exception as ex:  # noqa
                result.append(([], {}, str(ex)))

        def wait():                          # Tk calls only from this thread: poll for the result
            if not self.winfo_exists():
                return
            if result:
                self._show(*result[0])
            else:
                self.after(100, wait)
        threading.Thread(target=work, daemon=True).start()
        self.after(100, wait)

    def _show(self, found, names, err):
        if not self.winfo_exists():
            return
        self.findings = found
        self.rows = {}
        for f in found:
            best = f.best
            iid = self.tree.insert("", "end", values=(
                names.get(id(f)) or "", f.rel, f"{f.emu} · {f.label}", _when(f.mtime),
                f"{best.describe()} ({_human(best.size)})" if best else "No copy found"),
                tags=("copy" if best else "none",))
            self.rows[iid] = f
        with_copy = sum(1 for f in found if f.copies)
        if err:
            text = f"The search stopped: {err}"
        elif not found:
            text = "No empty save files: every save in your emulator folders has data in it."
        else:
            text = (f"{len(found)} empty save file(s); {with_copy} can be put back from a kept copy. "
                    + ("The rest have no copy here: check the other device or the cloud's "
                       "EmuSaveManager-overwritten folder." if with_copy < len(found) else ""))
        self.summary.config(text=text)
        self.b_all.config(state="normal" if with_copy else "disabled")
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])

    def show_copies(self):
        self.copies.delete(*self.copies.get_children())
        self.copy_rows = {}
        sel = self.tree.selection()
        f = self.rows.get(sel[0]) if len(sel) == 1 else None
        if f:
            for c in f.copies:
                self.copy_rows[self.copies.insert("", "end", values=(c.describe(), _human(c.size)))] = c
        kids = self.copies.get_children()
        if kids:
            self.copies.selection_set(kids[0])
        self.b_one.config(state="normal" if kids else "disabled")

    # ---- restoring ------------------------------------------------------------------
    def _restore(self, pairs):
        done, problems = 0, []
        backup = Path(self.app.backup_dir)
        for f, c in pairs:
            try:
                if c.kind == "history":
                    hist = sh.SaveHistory(sh.history_dir(backup), self.app.cfg.get("history", {}))
                    es.restore(f, c, backup)               # the empty file goes to empty_saves first
                    hist.snapshot(sh.location_key(f.emu, f.label), f.folder, "restored")
                    hist.save()
                else:
                    es.restore(f, c, backup)
                done += 1
            except Exception as ex:  # noqa
                problems.append(f"{f.rel}: {ex}")
        msg = f"Put back {done} save(s). Sync now to send them to your other devices."
        if problems:
            msg += "\n\nNot restored:\n" + "\n".join(problems[:12])
        (messagebox.showwarning if problems else messagebox.showinfo)(TITLE, msg, parent=self)
        self.scan()

    def restore_selected_copy(self):
        sel, csel = self.tree.selection(), self.copies.selection()
        if len(sel) != 1 or not csel:
            return
        f, c = self.rows[sel[0]], self.copy_rows[csel[0]]
        if messagebox.askyesno(TITLE, f"Put back {f.rel} from:\n{c.describe()} ({_human(c.size)})?",
                               parent=self):
            self._restore([(f, c)])

    def restore_all(self):
        pairs = [(f, f.best) for f in self.findings if f.best]
        if pairs and messagebox.askyesno(
                TITLE, f"Put back {len(pairs)} save(s), each from its newest kept copy?", parent=self):
            self._restore(pairs)

    def open_folder(self):
        sel = self.tree.selection()
        f = self.rows.get(sel[0]) if sel else None
        if f and hasattr(self.app, "open_folder"):
            self.app.open_folder(f.path.parent)
