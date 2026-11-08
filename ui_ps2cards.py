"""ui_ps2cards.py - window for browsing and editing PCSX2 memory card images."""

import os
import shutil
import struct
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ps2mc import CardError, PS2Card, build_psu, parse_psu

CARD_EXTS = {".ps2", ".bin", ".mc2"}
KEEP_SAFETY_COPIES = 10


class PS2CardDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("PS2 Memory Card Manager")
        self.geometry("960x580")
        self.minsize(780, 440)
        self.transient(app)
        self.path = None
        self.warned = False
        self.cards = {}
        self._build()
        self.refresh_card_list()

    # ---- UI ---------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 2))
        top.pack(fill="x")
        ttk.Label(top, text="Card:").pack(side="left")
        self.combo = ttk.Combobox(top, state="readonly", width=42)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", self.on_pick)
        ttk.Button(top, text="Browse…", command=self.browse_card).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh_card_list).pack(side="left", padx=4)

        ops = ttk.Frame(self, padding=(10, 2, 10, 6))
        ops.pack(fill="x")
        for text, cmd in (("New 8 MB card…", self.new_card), ("Duplicate…", self.duplicate_card),
                          ("Rename…", self.rename_card), ("Delete card file…", self.delete_card),
                          ("Open folder", self.open_folder)):
            ttk.Button(ops, text=text, command=cmd).pack(side="left", padx=(0, 4))

        frame = ttk.LabelFrame(self, text="Saves on this card", padding=6)
        frame.pack(fill="both", expand=True, padx=10, pady=4)
        cols = ("dir", "title", "size", "files", "modified", "flags")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, text, w, anchor in (("dir", "Save folder", 190, "w"), ("title", "Title", 280, "w"),
                                   ("size", "Size", 70, "e"), ("files", "Files", 50, "e"),
                                   ("modified", "Modified (JST)", 140, "w"), ("flags", "Flags", 90, "w")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=anchor)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.info = ttk.Label(self, text="", padding=(10, 0))
        self.info.pack(anchor="w")
        btns = ttk.Frame(self, padding=(10, 6, 10, 10))
        btns.pack(fill="x")
        for text, cmd in (("Export selected (.psu)…", self.export_selected),
                          ("Import .psu…", self.import_psu),
                          ("Copy to another card…", self.copy_to_card),
                          ("Delete selected", self.delete_selected)):
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=(0, 4))

    # ---- card list ------------------------------------------------------------
    def card_dir(self):
        return self.app.resolve("PCSX2").get("Memory Cards")

    def refresh_card_list(self):
        self.cards = {}
        d = self.card_dir()
        if d and d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() in CARD_EXTS:
                    self.cards[f"{p.name}  ({p.stat().st_size / 1048576:.1f} MB)"] = p
        if self.path and self.path.is_file() and self.path not in self.cards.values():
            self.cards[f"{self.path.name}  (other)"] = self.path
        names = list(self.cards)
        self.combo["values"] = names
        current = next((n for n, p in self.cards.items() if p == self.path), None)
        if current:
            self.combo.set(current)
        elif names:
            self.combo.set(names[0])
            self.path = self.cards[names[0]]
        else:
            self.combo.set("")
            self.path = None
        self.reload()

    def on_pick(self, _evt=None):
        self.path = self.cards.get(self.combo.get())
        self.reload()

    def browse_card(self):
        d = self.card_dir()
        f = filedialog.askopenfilename(parent=self, initialdir=str(d) if d else str(Path.home()),
                                       filetypes=[("PS2 memory cards", "*.ps2 *.bin *.mc2"), ("All files", "*")])
        if f:
            self.path = Path(f)
            self.refresh_card_list()

    # ---- display ----------------------------------------------------------------
    def reload(self):
        self.tree.delete(*self.tree.get_children())
        self.info.config(text="No card selected." if not self.path else "")
        if not self.path:
            return
        try:
            card = PS2Card.load(self.path)
            saves = card.list_saves()
            free = card.free_clusters()
        except (CardError, OSError, struct.error) as ex:
            self.info.config(text=f"Cannot read {self.path.name}: {ex}")
            return
        for s in saves:
            flags = ", ".join(f for f, on in (("Protected", s.protected), ("PS1", s.psx),
                                              ("Hidden", s.hidden)) if on)
            title = s.title or ("(PS1 save)" if s.psx else "")
            self.tree.insert("", "end", iid=s.dirname, values=(
                s.dirname, title, f"{s.size_kb} KB", s.files,
                s.modified.strftime("%Y-%m-%d %H:%M") if s.modified else "", flags))
        self.info.config(text=f"{len(saves)} save(s)  ·  {free} KB free  ·  "
                              f"{'with' if card.has_ecc else 'without'} ECC data")

    # ---- safe modification ------------------------------------------------------------
    def _safety_copy(self, path: Path):
        d = self.app.backup_dir / "_ps2_card_safety"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        shutil.copy2(path, d / f"{path.stem}_{stamp}{path.suffix}")
        old = sorted(d.glob(f"{path.stem}_*{path.suffix}"))
        for f in old[:-KEEP_SAFETY_COPIES]:
            try:
                f.unlink()
            except OSError:
                pass

    def _confirm_closed(self):
        if self.warned:
            return True
        ok = messagebox.askokcancel(
            "Close PCSX2 first",
            "Changing a memory card while PCSX2 is running can corrupt it.\n\n"
            "Close PCSX2 (or eject the card in Settings > Memory Cards), then press OK.\n"
            "A safety copy of the card is made before every change.", parent=self)
        self.warned = ok
        return ok

    def _modify(self, path: Path, func) -> bool:
        if not self._confirm_closed():
            return False
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self._safety_copy(path)
            card = PS2Card.load(path)
            func(card)
            card.save(path)
            return True
        except (CardError, OSError, struct.error) as ex:
            messagebox.showerror("Memory card", f"The card was not changed:\n\n{ex}", parent=self)
            return False
        finally:
            self.config(cursor="")

    def _need_card(self):
        if not self.path:
            messagebox.showinfo("Memory card", "Select a memory card first.", parent=self)
            return False
        return True

    def _selected(self):
        sel = list(self.tree.selection())
        if not sel:
            messagebox.showinfo("Memory card", "Select one or more saves first.", parent=self)
        return sel

    # ---- save operations ------------------------------------------------------------------
    def export_selected(self):
        if not self._need_card():
            return
        names = self._selected()
        if not names:
            return
        dest = filedialog.askdirectory(parent=self, title="Export .psu files to…")
        if not dest:
            return
        try:
            card = PS2Card.load(self.path)
            done = []
            for n in names:
                target = Path(dest) / f"{n}.psu"
                i = 2
                while target.exists():
                    target = Path(dest) / f"{n}_{i}.psu"
                    i += 1
                target.write_bytes(build_psu(card.get_save(n)))
                done.append(target.name)
        except (CardError, OSError) as ex:
            messagebox.showerror("Export", str(ex), parent=self)
            return
        messagebox.showinfo("Export", f"Exported {len(done)} save(s) to:\n{dest}", parent=self)

    def _import_into(self, target: Path, saves) -> int:
        """Import PSUSave objects into a card file, asking before overwriting."""
        existing = {s.dirname for s in PS2Card.load(target).list_saves()}
        overwrite = set()
        chosen = []
        for s in saves:
            if s.dirname in existing:
                ans = messagebox.askyesnocancel(
                    "Already on card", f"'{s.dirname}' already exists on the card.\n\n"
                    "Yes = replace it, No = skip it, Cancel = stop.", parent=self)
                if ans is None:
                    return -1
                if not ans:
                    continue
                overwrite.add(s.dirname)
            chosen.append(s)
        if not chosen:
            return 0

        def work(card):
            for s in chosen:
                card.import_save(s, overwrite=s.dirname in overwrite)

        return len(chosen) if self._modify(target, work) else -1

    def import_psu(self):
        if not self._need_card():
            return
        files = filedialog.askopenfilenames(parent=self, title="Choose .psu save files",
                                            filetypes=[("PS2 saves", "*.psu"), ("All files", "*")])
        if not files:
            return
        saves = []
        for f in files:
            try:
                saves.append(parse_psu(Path(f).read_bytes()))
            except (CardError, OSError) as ex:
                messagebox.showerror("Import", f"{Path(f).name}: {ex}", parent=self)
                return
        n = self._import_into(self.path, saves)
        self.reload()
        if n >= 0:
            self.info.config(text=self.info.cget("text") + f"  ·  imported {n}")

    def copy_to_card(self):
        if not self._need_card():
            return
        names = self._selected()
        if not names:
            return
        d = self.card_dir()
        f = filedialog.askopenfilename(parent=self, title="Copy to which card?",
                                       initialdir=str(d) if d else str(Path.home()),
                                       filetypes=[("PS2 memory cards", "*.ps2 *.bin *.mc2")])
        if not f or Path(f) == self.path:
            return
        try:
            src = PS2Card.load(self.path)
            saves = [src.get_save(n) for n in names]
        except (CardError, OSError) as ex:
            messagebox.showerror("Copy", str(ex), parent=self)
            return
        n = self._import_into(Path(f), saves)
        if n >= 0:
            messagebox.showinfo("Copy", f"Copied {n} save(s) to {Path(f).name}.", parent=self)

    def delete_selected(self):
        if not self._need_card():
            return
        names = self._selected()
        if not names:
            return
        if not messagebox.askyesno("Delete saves", "Permanently delete from the card:\n\n  "
                                   + "\n  ".join(names) + "\n\nA safety copy of the card is kept.",
                                   parent=self):
            return

        def work(card):
            for n in names:
                card.delete_save(n)

        self._modify(self.path, work)
        self.reload()

    # ---- card file operations ------------------------------------------------------------------
    def _ask_new_path(self, prompt, initial):
        d = self.card_dir() or (self.path.parent if self.path else None)
        if not d:
            messagebox.showinfo("Memory card", "PCSX2's memory card folder was not found.\n"
                                "Set it with 'Locations…' in the main window.", parent=self)
            return None
        name = simpledialog.askstring("Memory card", prompt, initialvalue=initial, parent=self)
        if not name:
            return None
        name = name.strip()
        if not Path(name).suffix:
            name += ".ps2"
        if os.sep in name or "/" in name or name.startswith("."):
            messagebox.showerror("Memory card", "Please enter a plain file name.", parent=self)
            return None
        target = d / name
        if target.exists():
            messagebox.showerror("Memory card", f"{name} already exists.", parent=self)
            return None
        return target

    def new_card(self):
        target = self._ask_new_path("Name for the new 8 MB card:", "Mcd003.ps2")
        if not target:
            return
        try:
            PS2Card.create_blank().save(target)
        except OSError as ex:
            messagebox.showerror("New card", str(ex), parent=self)
            return
        self.path = target
        self.refresh_card_list()

    def duplicate_card(self):
        if not self._need_card():
            return
        target = self._ask_new_path("Name for the copy:", f"{self.path.stem}_copy{self.path.suffix}")
        if target:
            try:
                shutil.copy2(self.path, target)
            except OSError as ex:
                messagebox.showerror("Duplicate", str(ex), parent=self)
                return
            self.path = target
            self.refresh_card_list()

    def rename_card(self):
        if not self._need_card() or not self._confirm_closed():
            return
        target = self._ask_new_path("New file name:", self.path.name)
        if target:
            try:
                self.path.rename(target)
            except OSError as ex:
                messagebox.showerror("Rename", str(ex), parent=self)
                return
            self.path = target
            self.refresh_card_list()

    def delete_card(self):
        if not self._need_card() or not self._confirm_closed():
            return
        if messagebox.askyesno("Delete card file", f"Delete {self.path.name} and ALL saves on it?\n"
                               "A safety copy is kept in the backup folder.", parent=self):
            try:
                self._safety_copy(self.path)
                self.path.unlink()
            except OSError as ex:
                messagebox.showerror("Delete", str(ex), parent=self)
                return
            self.path = None
            self.refresh_card_list()

    def open_folder(self):
        d = self.card_dir()
        if d and d.is_dir():
            self.app.open_folder(d)
