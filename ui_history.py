"""ui_history.py - browse earlier versions of saves and put any of them back."""

import hashlib
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import autosync
import savehistory as sh
import savenames

TITLE = "Save History"


def _human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _sha(path: Path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


class SaveHistoryDialog(tk.Toplevel):
    def __init__(self, app, emu=None):
        super().__init__(app)
        self.app = app
        self.title(f"{TITLE} - earlier versions of your saves")
        self.geometry("1040x700")
        self.minsize(860, 560)
        self.transient(app)
        self.hcfg = app.cfg.setdefault("history", {})
        self.locs = {}                    # display name -> (key, folder or None)
        self.file_rows = {}               # iid -> rel
        self.version_rows = {}            # iid -> version
        self.point_rows = []              # [time]
        self._build()
        self._load_locations(emu)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ---- UI -------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Save folder:").pack(side="left")
        self.v_loc = tk.StringVar()
        self.combo = ttk.Combobox(top, textvariable=self.v_loc, width=34, state="readonly")
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(top, text="Take snapshot now", command=self.snapshot_now).pack(side="left", padx=(6, 0))
        self.folder_label = ttk.Label(top, text="", foreground="#666")
        self.folder_label.pack(side="left", padx=10)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10)

        lf = ttk.LabelFrame(panes, text="Files with history", padding=6)
        panes.add(lf, weight=1)
        self.files = ttk.Treeview(lf, columns=("game", "file", "n", "last"), show="headings",
                                  selectmode="browse")
        for c, text, w, a in (("game", "Game", 220, "w"), ("file", "File", 170, "w"), ("n", "Versions", 70, "e"),
                              ("last", "Last change", 140, "w")):
            self.files.heading(c, text=text)
            self.files.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.files.yview)
        self.files.configure(yscrollcommand=sb.set)
        self.files.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.files.bind("<<TreeviewSelect>>", lambda e: self.show_versions())

        rf = ttk.LabelFrame(panes, text="Versions of the selected file (newest first)", padding=6)
        panes.add(rf, weight=1)
        self.vers = ttk.Treeview(rf, columns=("when", "size", "what"), show="headings", selectmode="browse")
        for c, text, w, a in (("when", "Kept at", 150, "w"), ("size", "Size", 80, "e"),
                              ("what", "What happened", 230, "w")):
            self.vers.heading(c, text=text)
            self.vers.column(c, width=w, anchor=a)
        self.vers.tag_configure("current", foreground="#1f7a1f")
        self.vers.pack(fill="both", expand=True)
        vb = ttk.Frame(rf)
        vb.pack(fill="x", pady=(6, 0))
        ttk.Button(vb, text="Restore this version", command=self.restore_version).pack(side="left")
        ttk.Button(vb, text="Save a copy…", command=self.export_version).pack(side="left", padx=6)

        whole = ttk.LabelFrame(self, text="Whole folder (use this for PCSX2 folder memory cards and "
                                          "anything made of several files)", padding=8)
        whole.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(whole, text="Put the folder back the way it was at:").pack(side="left")
        self.v_point = tk.StringVar()
        self.points = ttk.Combobox(whole, textvariable=self.v_point, width=46, state="readonly")
        self.points.pack(side="left", padx=6)
        ttk.Button(whole, text="Restore folder…", command=self.restore_folder).pack(side="left")

        st = ttk.LabelFrame(self, text="Settings", padding=8)
        st.pack(fill="x", padx=10, pady=(8, 0))
        s = sh.settings_from(self.hcfg)
        self.v_on = tk.BooleanVar(value=bool(s["enabled"]))
        self.v_keep = tk.StringVar(value=str(s["keep"]))
        self.v_max = tk.StringVar(value=str(round(float(s["max_mb"]) / 1024, 1)).rstrip("0").rstrip("."))
        ttk.Checkbutton(st, text="Keep earlier versions at every sync", variable=self.v_on,
                        command=self.save_settings).pack(side="left")
        ttk.Label(st, text="   Keep").pack(side="left")
        sp = ttk.Spinbox(st, from_=1, to=100, width=4, textvariable=self.v_keep, command=self.save_settings)
        sp.pack(side="left", padx=4)
        sp.bind("<FocusOut>", lambda e: self.save_settings())
        ttk.Label(st, text="versions per file, using at most").pack(side="left")
        mx = ttk.Spinbox(st, from_=0.5, to=100, increment=0.5, width=5, textvariable=self.v_max,
                         command=self.save_settings)
        mx.pack(side="left", padx=4)
        mx.bind("<FocusOut>", lambda e: self.save_settings())
        ttk.Label(st, text="GB").pack(side="left")
        self.usage = ttk.Label(st, text="", foreground="#1f5fa8")
        self.usage.pack(side="right")

        note = ("A restored file gets today's date, so the next sync uploads it and your other device "
                "picks it up. What it replaces is kept here first, so a restore can be undone too. "
                "Close the emulator before restoring.")
        ttk.Label(self, text=note, wraplength=1000, foreground="#555", padding=(10, 6)).pack(anchor="w")
        self.status = ttk.Label(self, text="", padding=(10, 0, 10, 8))
        self.status.pack(anchor="w")

    # ---- data -----------------------------------------------------------------
    def _history(self):
        return sh.SaveHistory(sh.history_dir(self.app.backup_dir), self.hcfg)

    def _load_locations(self, emu=None):
        h = self._history()
        self.locs = {}
        for name, prof in self.app.profiles.items():
            resolved = self.app.resolve(name)
            for loc in prof["locations"]:
                key = sh.location_key(name, loc["label"])
                folder = resolved.get(loc["label"])
                if (folder and Path(folder).is_dir()) or key in h.index["locations"]:
                    self.locs[f"{name} / {loc['label']}"] = (key, folder)
        for key in h.keys():                     # e.g. a custom emulator removed since
            if not any(k == key for k, _f in self.locs.values()):
                self.locs[key.replace("::", " / ")] = (key, None)
        names = list(self.locs)
        self.combo["values"] = names
        pick = next((n for n in names if emu and n.startswith(f"{emu} / ")), None)
        pick = pick or next((n for n in names if self.locs[n][0] in h.index["locations"]), None)
        if names:
            self.v_loc.set(pick or names[0])
        self.refresh(h)

    def _current(self):
        return self.locs.get(self.v_loc.get(), (None, None))

    def _folder(self, h, key, folder):
        if folder:
            return Path(folder)
        loc = h.index["locations"].get(key) or {}
        return Path(loc["folder"]) if loc.get("folder") else None

    def refresh(self, h=None):
        h = h or self._history()
        key, folder = self._current()
        self.files.delete(*self.files.get_children())
        self.vers.delete(*self.vers.get_children())
        self.file_rows, self.version_rows = {}, {}
        f = self._folder(h, key, folder) if key else None
        self.folder_label.config(text=str(f) if f else "")
        if key:
            rows = list(h.files(key))
            names = savenames.for_location(self.app, key, f, [rel for rel, _n, _l in rows])
            for i, (rel, n, last) in enumerate(rows):
                iid = f"f{i}"
                self.file_rows[iid] = rel
                self.files.insert("", "end", iid=iid, values=(names.name(rel), rel, n, sh.fmt_time(last)[:16]))
            loc = h.index["locations"].get(key) or {"files": {}}
            counts = {}
            for e in loc["files"].values():
                for v in e["versions"]:
                    counts[v["taken"]] = counts.get(v["taken"], 0) + 1
            self.point_rows = h.points(key)
            self.points["values"] = [f"{sh.fmt_time(t)}   ({counts.get(t, 0)} file(s) changed)"
                                     for t in self.point_rows]
            self.v_point.set(self.points["values"][0] if self.point_rows else "")
        s = sh.settings_from(self.hcfg)
        self.usage.config(text=f"History uses {_human(h.usage())} of {_human(float(s['max_mb']) * 1048576)}"
                          if s["enabled"] else "History is switched off")
        if not self.file_rows:
            self.status.config(text="No history for this folder yet. It starts with the next sync, or use "
                                    "'Take snapshot now'.")
        else:
            self.status.config(text="")

    def _selected_rel(self):
        sel = self.files.selection()
        return self.file_rows.get(sel[0]) if sel else None

    def show_versions(self):
        self.vers.delete(*self.vers.get_children())
        self.version_rows = {}
        rel = self._selected_rel()
        key, folder = self._current()
        if not rel or not key:
            return
        h = self._history()
        f = self._folder(h, key, folder)
        cur = _sha(f / rel) if f and (f / rel).is_file() else None
        for i, v in enumerate(h.versions(key, rel)):
            iid = f"v{i}"
            self.version_rows[iid] = v
            what = sh.REASONS.get(v["reason"], v["reason"])
            tags = ()
            if cur and v["sha"] == cur:
                what += "  (= the file now)"
                tags = ("current",)
            self.vers.insert("", "end", iid=iid, values=(sh.fmt_time(v["taken"]), _human(v["size"]), what),
                             tags=tags)

    # ---- actions --------------------------------------------------------------
    def _guard(self):
        """True when it's safe to write into save folders right now."""
        emus = autosync.running_emulators()
        if emus:
            messagebox.showwarning(TITLE, f"Close the emulator first ({', '.join(emus)} is running).",
                                   parent=self)
            return None
        lock = autosync.SyncLock(kind="restore")
        if not lock.acquire():
            messagebox.showinfo(TITLE, "A sync is running right now. Try again in a minute.", parent=self)
            return None
        return lock

    def restore_version(self):
        rel = self._selected_rel()
        sel = self.vers.selection()
        if not rel or not sel:
            messagebox.showinfo(TITLE, "Pick a file on the left and one of its versions first.", parent=self)
            return
        v = self.version_rows[sel[0]]
        key, folder = self._current()
        h = self._history()
        f = self._folder(h, key, folder)
        if not f:
            messagebox.showerror(TITLE, "This save folder is not found on this device.", parent=self)
            return
        if not messagebox.askyesno(TITLE, f"Put back {rel} as it was at {sh.fmt_time(v['taken'])}?\n\n"
                                          f"Into: {f}\n\nThe current file is kept in the history first.",
                                   parent=self):
            return
        self._do(lambda: h.restore_file(key, rel, v, f), rel)

    def restore_folder(self):
        key, folder = self._current()
        idx = self.points.current()
        if not key or idx < 0 or idx >= len(self.point_rows):
            messagebox.showinfo(TITLE, "There is no earlier state of this folder yet.", parent=self)
            return
        when = self.point_rows[idx]
        h = self._history()
        f = self._folder(h, key, folder)
        if not f:
            messagebox.showerror(TITLE, "This save folder is not found on this device.", parent=self)
            return
        state = h.state_at(key, when)
        if not messagebox.askyesno(TITLE, f"Put {len(state)} file(s) in\n{f}\nback the way they were at "
                                          f"{sh.fmt_time(when)}?\n\nFiles that changed since then are kept in "
                                          f"the history first. Files that didn't exist then are left as they "
                                          f"are.", parent=self):
            return
        self._do(lambda: h.restore_folder(key, when, f), "the folder")

    def _do(self, func, what):
        lock = self._guard()
        if not lock:
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            n = func()
        except Exception as ex:  # noqa
            messagebox.showerror(TITLE, f"Restore failed:\n\n{ex}", parent=self)
            return
        finally:
            lock.release()
            self.config(cursor="")
        self.refresh()
        try:
            self.app.refresh_all()
        except Exception:  # noqa - the main window may be busy
            pass
        msg = (f"Restored {n} file(s)." if n else f"Nothing to do: {what} already matches that version.")
        self.status.config(text=msg + (" It will be uploaded at the next sync." if n else ""))
        messagebox.showinfo(TITLE, msg, parent=self)

    def export_version(self):
        rel = self._selected_rel()
        sel = self.vers.selection()
        if not rel or not sel:
            messagebox.showinfo(TITLE, "Pick a file on the left and one of its versions first.", parent=self)
            return
        v = self.version_rows[sel[0]]
        stamp = sh.fmt_time(v["taken"]).replace(":", "-").replace(" ", "_")
        p = Path(rel)
        dest = filedialog.asksaveasfilename(parent=self, initialfile=f"{p.stem}_{stamp}{p.suffix}")
        if dest:
            try:
                self._history().export(v, dest)
                self.status.config(text=f"Saved a copy to {dest}")
            except OSError as ex:
                messagebox.showerror(TITLE, str(ex), parent=self)

    def snapshot_now(self):
        key, folder = self._current()
        if not key or not folder or not Path(folder).is_dir():
            messagebox.showinfo(TITLE, "This save folder is not found on this device.", parent=self)
            return
        h = self._history()
        if not h.settings["enabled"]:
            messagebox.showinfo(TITLE, "History is switched off (see Settings below).", parent=self)
            return
        n = h.snapshot(key, folder, "manual")
        h.prune()
        h.save()
        self.refresh(h)
        self.status.config(text=f"Snapshot taken: {n} changed file(s) kept." if n
                           else "Snapshot taken: nothing changed since the last one.")

    def save_settings(self):
        try:
            keep = max(1, min(100, int(float(self.v_keep.get()))))
            max_mb = max(512, int(float(self.v_max.get()) * 1024))
        except ValueError:
            return
        # lower limits take effect at the next sync, so a slip of the spinbox loses nothing now
        self.hcfg.update(enabled=bool(self.v_on.get()), keep=keep, max_mb=max_mb)
        self.app.save_cfg()
        self.refresh()
