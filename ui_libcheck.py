"""ui_libcheck.py - the ROM Library's Check-up window: a report, nothing is changed."""

import tkinter as tk
from tkinter import filedialog, ttk

import apptheme
import libcheck
import romlibrary as L

ALL = "All systems"


class CheckupDialog(tk.Toplevel):
    """Lists libcheck findings by category. 'Show in library' selects the entries in the main window."""

    def __init__(self, app, items=None):
        super().__init__(app)
        self.app = app
        self.title("Library check-up")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1000, sw - 40)}x{min(640, sh - 90)}")
        self.transient(app)
        self.rows = {}                       # iid -> Finding
        self.report = None
        self._items = items
        self._build()
        self.run()
        self.bind("<Escape>", lambda e: self.destroy())

    # ---- layout ---------------------------------------------------------------
    def _build(self):
        col = apptheme.colors(self)
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        self.summary = ttk.Label(top, text="", wraplength=900)
        self.summary.pack(side="left", fill="x", expand=True)
        bar = ttk.Frame(self, padding=(10, 0, 10, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="System:").pack(side="left")
        self.v_sys = tk.StringVar(value=ALL)
        self.sys_box = ttk.Combobox(bar, textvariable=self.v_sys, state="readonly", width=28)
        self.sys_box.pack(side="left", padx=(4, 12))
        self.sys_box.bind("<<ComboboxSelected>>", lambda e: self.fill())
        ttk.Button(bar, text="Check again", command=self.run).pack(side="left")
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Save report…", command=self.save_report).pack(side="right", padx=(0, 8))

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=10)
        tf = ttk.Frame(panes)
        panes.add(tf, weight=3)
        style = "Lib.Treeview" if apptheme.is_library(self) else "Treeview"
        self.tree = ttk.Treeview(tf, columns=("system", "size"), selectmode="extended", style=style)
        self.tree.heading("#0", text="What")
        self.tree.heading("system", text="System")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=520)
        self.tree.column("system", width=180)
        self.tree.column("size", width=100, anchor="e")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show())
        self.tree.bind("<Double-1>", lambda e: self.show_in_library())

        bf = ttk.Frame(panes)
        panes.add(bf, weight=1)
        self.text = tk.Text(bf, height=6, wrap="word", relief="flat", padx=8, pady=6, font="TkDefaultFont",
                            background=col["log"], foreground=col["log_fg"], insertbackground=col["log_fg"])
        self.text.pack(fill="both", expand=True, pady=(6, 0))
        self.text.configure(state="disabled")
        act = ttk.Frame(self, padding=10)
        act.pack(fill="x")
        self.show_btn = ttk.Button(act, text="Show in library", command=self.show_in_library, state="disabled")
        self.show_btn.pack(side="left")
        ttk.Label(act, text="The check-up only reports: it doesn't move, change or hide anything.",
                  foreground=col["dim"]).pack(side="right")

    # ---- data -------------------------------------------------------------------
    def _all_items(self):
        if self._items is not None:
            return list(self._items)
        lib = getattr(self.app, "lib", None)
        if not lib:
            return []
        with lib.lock:
            return list(lib.items.values())

    def _shadowed(self):
        lib = getattr(self.app, "lib", None)
        return list(getattr(lib, "shadowed", []) or []) if lib and self._items is None else []

    def run(self):
        self.report = libcheck.check(self._all_items(), self._shadowed())
        systems = sorted({f.system_name() for f in self.report.findings}, key=str.lower)
        self.sys_box["values"] = [ALL] + systems
        if self.v_sys.get() not in self.sys_box["values"]:
            self.v_sys.set(ALL)
        self.summary.config(text=libcheck.summary(self.report))
        self.fill()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        want = self.v_sys.get()
        cats = self.report.by_category()
        n = 0
        for cat, title, _help in libcheck.CATEGORIES:
            found = [f for f in cats.get(cat, []) if want in (ALL, f.system_name())]
            if not found:
                continue
            total = sum(f.size for f in found)
            parent = self.tree.insert("", "end", iid=cat, text=f"{title} ({len(found)})", open=len(found) <= 40,
                                      values=("", L.human(total) if total else ""))
            for f in sorted(found, key=lambda f: (f.system_name().lower(), f.title.lower())):
                iid = f"r{n}"
                n += 1
                self.rows[iid] = f
                self.tree.insert(parent, "end", iid=iid, text=f.title,
                                 values=(f.system_name(), L.human(f.size) if f.size else ""))
        first = self.tree.get_children()
        if first:
            self.tree.selection_set(first[0])
            self.tree.focus(first[0])
        self.show()

    def selected_findings(self):
        out = []
        for iid in self.tree.selection():
            if iid in self.rows:
                out.append(self.rows[iid])
            else:
                out += [self.rows[c] for c in self.tree.get_children(iid) if c in self.rows]
        return out

    def show(self):
        sel = self.tree.selection()
        txt = ""
        if sel and sel[0] in self.rows:
            f = self.rows[sel[0]]
            lines = [f"{f.title}  ·  {f.system_name()}", "", f.detail, ""]
            lines += ["In the library:"] + [f"  {k}" for k in f.keys]
            txt = "\n".join(lines)
        elif sel and sel[0] in libcheck.CATEGORY_HELP:
            txt = libcheck.CATEGORY_HELP[sel[0]]
        elif not self.rows:
            txt = ("Nothing to report: everything looks like a game in a format its emulator reads."
                   if self.report and self.report.checked else
                   "The library hasn't been listed yet. Use Refresh in the main window first.")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", txt)
        self.text.configure(state="disabled")
        keys = [k for f in self.selected_findings() for k in f.keys]
        self.show_btn.configure(state="normal" if keys and hasattr(self.app, "show_keys") else "disabled")

    def show_in_library(self):
        keys = [k for f in self.selected_findings() for k in f.keys]
        if keys and hasattr(self.app, "show_keys"):
            self.app.show_keys(keys)

    def save_report(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Save the check-up report",
                                            defaultextension=".txt", initialfile="library-checkup.txt",
                                            filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.report.text())
