"""ui_ps2cards.py - two-pane manager for PCSX2 memory cards.

Two cards are shown side by side - card files (.ps2) or PCSX2 folder memory
cards, in any combination.  Saves can be copied or moved between them with the
arrow buttons or by dragging (hold Shift while dropping to move).
Before a card file is changed a safety copy of it is made; on folder cards the
save folders that are replaced or removed are moved to the backup folder.
"""

import os
import shutil
import struct
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ps2folder import create_folder_card, is_folder_card, open_card
from ps2mc import CardError, PS2Card, build_psu, parse_psu, transfer_saves, verify_saves

CARD_EXTS = {".ps2", ".bin", ".mc2"}
CARD_TYPES = [("PS2 memory cards", "*.ps2 *.bin *.mc2"), ("All files", "*")]
KEEP_SAFETY_COPIES = 10
ERRORS = (CardError, OSError, struct.error)


def same_file(a, b):
    try:
        return bool(a and b) and os.path.samefile(a, b)
    except OSError:
        return a == b


# ---------------------------------------------------------------------------
# One side of the manager
# ---------------------------------------------------------------------------
class CardPane(ttk.LabelFrame):
    COLS = (("dir", "Save folder", 115, "w", False), ("title", "Title", 150, "w", True),
            ("size", "Size", 60, "e", False), ("modified", "Modified (JST)", 128, "w", False))

    def __init__(self, master, dialog, label):
        super().__init__(master, text=label, padding=6)
        self.dialog = dialog
        self.label = label
        self.path = None
        self.cards = {}
        self.rows = {}          # dirname -> sort keys per column
        self.sort_col, self.sort_rev = "dir", False
        self.free_kb = 0

        row = ttk.Frame(self)
        row.pack(fill="x")
        self.combo = ttk.Combobox(row, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True)
        self.combo.bind("<<ComboboxSelected>>", self.on_pick)
        mb = ttk.Menubutton(row, text="Browse…")
        menu = tk.Menu(mb, tearoff=False)
        menu.add_command(label="Card file (.ps2)…", command=self.browse)
        menu.add_command(label="Folder memory card…", command=self.browse_folder)
        mb["menu"] = menu
        mb.pack(side="left", padx=(4, 0))

        tf = ttk.Frame(self)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings",
                                 selectmode="extended")
        for col, text, width, anchor, stretch in self.COLS:
            self.tree.heading(col, text=text, command=lambda c=col: self.sort_by(c))
            self.tree.column(col, width=width, anchor=anchor, stretch=stretch)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.usage = ttk.Progressbar(self, maximum=1, value=0)
        self.usage.pack(fill="x", pady=(6, 2))
        self.info = ttk.Label(self, text="", wraplength=480, justify="left")
        self.info.pack(anchor="w")

        self.tree.bind("<FocusIn>", lambda e: dialog.set_active(self))
        self.tree.bind("<<TreeviewSelect>>", lambda e: dialog.update_buttons())
        self.tree.bind("<Delete>", lambda e: dialog.delete_selected())
        self.tree.bind("<Double-1>", self.show_details)
        self.tree.bind("<Control-a>", self.select_all)
        self.tree.bind("<Control-A>", self.select_all)
        dialog.dnd.register(self)

    # ---- card picker ----------------------------------------------------------
    def set_cards(self, found):
        """found: {display name: path} of cards in PCSX2's folder."""
        self.cards = dict(found)
        if self.path and self.path.exists() and not any(same_file(p, self.path) for p in found.values()):
            self.cards[f"{self.path.name}  (other folder)"] = self.path
        names = list(self.cards)
        self.combo["values"] = names
        current = next((n for n, p in self.cards.items() if self.path and same_file(p, self.path)), None)
        if current:
            self.combo.set(current)
        else:
            self.combo.set("")
            self.path = None

    def on_pick(self, _evt=None):
        self.path = self.cards.get(self.combo.get())
        self.dialog.set_active(self)
        self.reload()

    def browse(self):
        d = self.dialog.card_dir()
        f = filedialog.askopenfilename(parent=self.dialog, filetypes=CARD_TYPES,
                                       initialdir=str(d) if d else str(Path.home()))
        if f:
            self.path = Path(f)
            self.dialog.set_active(self)
            self.dialog.refresh_card_lists()

    def browse_folder(self):
        d = self.dialog.card_dir()
        f = filedialog.askdirectory(parent=self.dialog, title="Choose a PCSX2 folder memory card",
                                    initialdir=str(d) if d else str(Path.home()))
        if not f:
            return
        if not is_folder_card(f):
            messagebox.showerror("Folder memory card", f"{Path(f).name} is not a PCSX2 folder "
                                 "memory card (it has no _pcsx2_superblock file).", parent=self.dialog)
            return
        self.path = Path(f)
        self.dialog.set_active(self)
        self.dialog.refresh_card_lists()

    # ---- contents ----------------------------------------------------------------
    def reload(self):
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        self.usage.configure(maximum=1, value=0)
        self.free_kb = 0
        if not self.path:
            self.info.config(text="No card selected.")
            self.dialog.update_buttons()
            return
        try:
            card = open_card(self.path)
            saves = card.list_saves()
            folder = getattr(card, "is_folder", False)
            self.free_kb = 0 if folder else card.free_clusters()
        except ERRORS as ex:
            self.info.config(text=f"Cannot read {self.path.name}: {ex}")
            self.dialog.update_buttons()
            return
        for s in saves:
            title = s.title or ("(PS1 save)" if s.psx else "")
            flags = [f for f, on in (("PS1", s.psx and s.title), ("protected", s.protected),
                                     ("hidden", s.hidden)) if on]
            if flags:
                title += f"  [{', '.join(flags)}]"
            mod = s.modified.strftime("%Y-%m-%d %H:%M") if s.modified else ""
            self.tree.insert("", "end", iid=s.dirname,
                             values=(s.dirname, title, f"{s.size_kb} KB", mod))
            self.rows[s.dirname] = {"dir": s.dirname.lower(), "title": title.lower(),
                                    "size": s.size_kb, "modified": mod}
        self._apply_sort()
        sel = [n for n in keep if n in self.rows]
        if sel:
            self.tree.selection_set(sel)
        if folder:
            self.usage.pack_forget()
            used = sum(s.size_kb for s in saves)
            self.info.config(text=f"Folder card  ·  {len(saves)} save(s)  ·  {used:,} KB  ·  PCSX2 "
                                  "loads only the running game's saves, so the 8 MB limit is per game"
                                  + ("" if card.formatted else "  ·  not formatted yet"))
        else:
            self.usage.pack(fill="x", pady=(6, 2), before=self.info)
            total = card.n_alloc
            self.usage.configure(maximum=total, value=total - self.free_kb)
            self.info.config(text=f"{len(saves)} save(s)  ·  {self.free_kb:,} KB free of {total:,} KB"
                                  f"{'' if card.has_ecc else '  ·  no ECC'}")
        self.dialog.update_buttons()

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if col == self.sort_col else False
        self.sort_col = col
        self._apply_sort()

    def _apply_sort(self):
        order = sorted(self.rows, key=lambda n: self.rows[n][self.sort_col], reverse=self.sort_rev)
        for i, n in enumerate(order):
            self.tree.move(n, "", i)
        for col, text, *_ in self.COLS:
            arrow = (" ▼" if self.sort_rev else " ▲") if col == self.sort_col else ""
            self.tree.heading(col, text=text + arrow)

    def selected(self):
        return list(self.tree.selection())

    def selected_kb(self):
        return sum(self.rows[n]["size"] for n in self.selected() if n in self.rows)

    def select_all(self, _evt=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def show_details(self, evt):
        name = self.tree.identify_row(evt.y)
        if not name or not self.path:
            return
        try:
            save = open_card(self.path).get_save(name)
        except ERRORS as ex:
            messagebox.showerror("Save details", str(ex), parent=self.dialog)
            return
        lines = [f"{f.name:<32} {len(f.data):>9,} bytes" for f in save.files]
        messagebox.showinfo(name, f"{save.title() or name}\n\n" + "\n".join(lines)
                            + f"\n\nTotal on card: {save.size_kb()} KB", parent=self.dialog)


# ---------------------------------------------------------------------------
# Drag and drop between the two save lists
# ---------------------------------------------------------------------------
class DragDrop:
    THRESHOLD = 6

    def __init__(self, dialog):
        self.dialog = dialog
        self.panes = []
        self.src = None
        self.start = (0, 0)
        self.dragging = False
        self.pending = None

    def register(self, pane):
        self.panes.append(pane)
        t = pane.tree
        t.bind("<ButtonPress-1>", lambda e, p=pane: self.press(e, p))
        t.bind("<B1-Motion>", lambda e, p=pane: self.motion(e, p))
        t.bind("<ButtonRelease-1>", lambda e, p=pane: self.release(e, p))

    def press(self, e, pane):
        self.src, self.dragging, self.pending = None, False, None
        if pane.tree.identify_region(e.x, e.y) not in ("cell", "tree"):
            return None
        row = pane.tree.identify_row(e.y)
        if not row:
            return None
        self.src = pane
        self.start = (e.x_root, e.y_root)
        # Clicking inside an existing multi-selection must not collapse it before a drag.
        if row in pane.tree.selection() and not e.state & 0x0005:
            self.pending = row
            pane.tree.focus_set()
            pane.tree.focus(row)
            return "break"
        return None

    def target(self, e):
        w = self.dialog.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            for p in self.panes:
                if w is p:
                    return p
            w = w.master
        return None

    def motion(self, e, pane):
        if self.src is not pane:
            return None
        if not self.dragging:
            if abs(e.x_root - self.start[0]) + abs(e.y_root - self.start[1]) < self.THRESHOLD:
                return "break" if self.pending else None
            self.dragging = True
        tgt = self.target(e)
        move = bool(e.state & 0x0001)
        if tgt and tgt is not pane:
            cursor = "exchange" if move else "plus"
            n = len(pane.selected())
            self.dialog.status(f"Drop to {'move' if move else 'copy'} {n} save(s) to "
                               f"{tgt.path.name if tgt.path else 'the other card'}"
                               f"{'' if move else '  (hold Shift to move)'}")
        else:
            cursor = "X_cursor"
        self.dialog.configure(cursor=cursor)
        pane.tree.configure(cursor=cursor)
        return "break"

    def release(self, e, pane):
        if self.src is not pane:
            return None
        src, self.src = self.src, None
        self.dialog.configure(cursor="")
        pane.tree.configure(cursor="")
        if not self.dragging:
            if self.pending:
                pane.tree.selection_set(self.pending)
                self.pending = None
                return "break"
            return None
        self.dragging = False
        self.pending = None
        tgt = self.target(e)
        self.dialog.status("")
        if tgt and tgt is not src:
            self.dialog.after_idle(lambda: self.dialog.transfer(src, tgt, bool(e.state & 0x0001)))
        return "break"


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
class PS2CardDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("PS2 Memory Card Manager")
        self.geometry("1200x660")
        self.minsize(960, 480)
        self.transient(app)
        self.warned = False
        self.dnd = DragDrop(self)
        self.active = None
        # questions and folder pickers go through these, so the console view can answer them
        self.msg = messagebox
        self.ask_directory = filedialog.askdirectory
        self.console = None
        self.view = "classic"
        self._styles()
        self._build()
        self.refresh_card_lists(initial=True)
        self.set_active(self.left)
        self.bind("<F2>", lambda e: self.set_view("console" if self.view == "classic" else "classic"))
        if (getattr(app, "cfg", {}) or {}).get("ps2_view") == "console":
            self.after(50, lambda: self.set_view("console"))

    # ---- UI ------------------------------------------------------------------------
    def _styles(self):
        style = ttk.Style(self)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self._bold = bold
        style.configure("Active.TLabelframe.Label", font=bold, foreground="#1f5fa8")
        style.configure("Active.TLabelframe", bordercolor="#1f5fa8")
        style.configure("Arrow.TButton", padding=(6, 6))

    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Card files:").pack(side="left", padx=(0, 6))
        for text, cmd in (("New 8 MB card…", self.new_card), ("New folder card…", self.new_folder_card),
                          ("Duplicate…", self.duplicate_card),
                          ("Rename…", self.rename_card), ("Delete card file…", self.delete_card)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Button(top, text="Refresh", command=self.refresh_card_lists).pack(side="right")
        ttk.Button(top, text="Console view (F2)", command=lambda: self.set_view("console")).pack(
            side="right", padx=(4, 8))
        self._top = top
        ttk.Button(top, text="Open folder", command=self.open_folder).pack(side="right", padx=4)

        body = ttk.Frame(self, padding=(10, 4, 10, 4))
        body.pack(fill="both", expand=True)
        self._body = body
        self.left = CardPane(body, self, "Left card")
        self.right = CardPane(body, self, "Right card")
        mid = ttk.Frame(body, padding=(8, 0))
        self.left.grid(row=0, column=0, sticky="nsew")
        mid.grid(row=0, column=1, sticky="ns")
        self.right.grid(row=0, column=2, sticky="nsew")
        body.columnconfigure(0, weight=1, uniform="pane")
        body.columnconfigure(2, weight=1, uniform="pane")
        body.rowconfigure(0, weight=1)

        ttk.Frame(mid).pack(expand=True)
        self.arrows = {}
        for key, text, args in (("copy_r", "Copy  →", (self.left, self.right, False)),
                                ("move_r", "Move  →", (self.left, self.right, True)),
                                (None, None, None),
                                ("copy_l", "←  Copy", (self.right, self.left, False)),
                                ("move_l", "←  Move", (self.right, self.left, True))):
            if key is None:
                ttk.Separator(mid).pack(fill="x", pady=10)
                continue
            b = ttk.Button(mid, text=text, width=10, style="Arrow.TButton",
                           command=lambda a=args: self.transfer(*a))
            b.pack(pady=3)
            self.arrows[key] = b
        ttk.Frame(mid).pack(expand=True)

        bottom = ttk.Frame(self, padding=(10, 4, 10, 2))
        bottom.pack(fill="x")
        self._bottom = bottom
        self.target_label = ttk.Label(bottom, text="Selected side:")
        self.target_label.pack(side="left", padx=(0, 6))
        for text, cmd in (("Export selected (.psu)…", self.export_selected),
                          ("Import .psu…", self.import_psu),
                          ("Delete selected", self.delete_selected)):
            ttk.Button(bottom, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Label(bottom, text="Drag saves between cards to copy · hold Shift to move · "
                               "double-click for details", foreground="gray").pack(side="right")

        self.status_bar = ttk.Label(self, text="", padding=(10, 2, 10, 8))
        self.status_bar.pack(fill="x")

    def status(self, text):
        self.status_bar.config(text=text)
        if self.view == "console" and self.console is not None:
            self.console.toast(text)

    # ---- views -----------------------------------------------------------------------
    def set_view(self, view):
        """'classic' (the two lists) or 'console' (icons, controller-friendly)."""
        if view == self.view:
            return
        if view == "console":
            try:
                import ui_ps2console
            except Exception as ex:  # noqa - e.g. a build without Pillow
                messagebox.showerror("Console view", f"The console view isn't available: {ex}", parent=self)
                return
            for f in (self._top, self._body, self._bottom, self.status_bar):
                f.pack_forget()
            if self.console is None:
                self.console = ui_ps2console.ConsoleView(self, self)
            self.console.pack(fill="both", expand=True)
            self.msg = self.console.prompts
            self.ask_directory = self._console_export_dir
            self.view = "console"
            self.console.start()
        else:
            if self.console is not None:
                self.console.stop()
                self.console.pack_forget()
            self._top.pack(fill="x")
            self._body.pack(fill="both", expand=True)
            self._bottom.pack(fill="x")
            self.status_bar.pack(fill="x")
            self.msg = messagebox
            self.ask_directory = filedialog.askdirectory
            self.view = "classic"
            self.reload_all()
        cfg = getattr(self.app, "cfg", None)
        if isinstance(cfg, dict) and cfg.get("ps2_view") != view:
            cfg["ps2_view"] = view
            try:
                from emu_save_manager import save_config
                save_config(cfg)
            except Exception:  # noqa - remembering the view is a nicety
                pass

    def _console_export_dir(self, **_kw):
        """The console view exports to a fixed folder (no file picker to drive with a controller)."""
        d = Path(self.app.backup_dir) / "PS2 exports"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def set_active(self, pane):
        if self.active is pane:
            return
        self.active = pane
        for p in (self.left, self.right):
            p.configure(style="Active.TLabelframe" if p is pane else "TLabelframe")
        self.target_label.config(text=f"{pane.label}:", font=self._bold)

    def update_buttons(self):
        if not hasattr(self, "arrows"):
            return
        both = (self.left.path and self.right.path and not same_file(self.left.path, self.right.path))
        for key, pane in (("copy_r", self.left), ("move_r", self.left),
                          ("copy_l", self.right), ("move_l", self.right)):
            ok = both and bool(pane.selected())
            self.arrows[key].state(["!disabled"] if ok else ["disabled"])

    # ---- card lists ------------------------------------------------------------------
    def card_dir(self):
        return self.app.resolve("PCSX2").get("Memory Cards")

    def found_cards(self):
        found = {}
        d = self.card_dir()
        if d and d.is_dir():
            for p in sorted(d.iterdir(), key=lambda p: p.name.lower()):
                if p.suffix.lower() not in CARD_EXTS:
                    continue
                if p.is_file():
                    found[f"{p.name}  ({p.stat().st_size / 1048576:.1f} MB file)"] = p
                elif is_folder_card(p):
                    found[f"{p.name}  (folder)"] = p
        return found

    def refresh_card_lists(self, initial=False):
        found = self.found_cards()
        paths = list(found.values())
        if initial:
            self.left.path = paths[0] if paths else None
            self.right.path = paths[1] if len(paths) > 1 else None
        for p in (self.left, self.right):
            p.set_cards(found)
            p.reload()
        if not paths and initial:
            self.status("No memory cards found in PCSX2's folder. Use Browse… or New 8 MB card…")
        self.update_buttons()

    def reload_all(self):
        for p in (self.left, self.right):
            p.reload()

    # ---- safe modification ----------------------------------------------------------------
    def _trash_dir(self, path: Path) -> Path:
        """Where save folders removed from a folder card go (one folder per change)."""
        root = self.app.backup_dir / "_ps2_card_safety" / "folder_cards"
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        t = root / f"{path.name}_{stamp}"
        i = 2
        while t.exists():
            t = root / f"{path.name}_{stamp}_{i}"
            i += 1
        olds = sorted((p for p in root.glob(f"{path.name}_*") if p.is_dir()),
                      key=lambda p: p.stat().st_mtime) if root.is_dir() else []
        for old in olds[:-KEEP_SAFETY_COPIES + 1]:
            shutil.rmtree(old, ignore_errors=True)
        return t

    def _write(self, card, path: Path):
        if getattr(card, "is_folder", False):
            card.save(trash_dir=self._trash_dir(path))
        else:
            card.save(path)

    def _safety_copy(self, path: Path):
        if path.is_dir():
            return              # folder cards: replaced/removed saves go to the trash folder
        d = self.app.backup_dir / "_ps2_card_safety"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        target = d / f"{path.stem}_{stamp}{path.suffix}"
        i = 2
        while target.exists():
            target = d / f"{path.stem}_{stamp}_{i}{path.suffix}"
            i += 1
        shutil.copy2(path, target)
        old = sorted(d.glob(f"{path.stem}_*{path.suffix}"), key=lambda f: f.stat().st_mtime)
        for f in old[:-KEEP_SAFETY_COPIES]:
            try:
                f.unlink()
            except OSError:
                pass

    def _confirm_closed(self):
        if self.warned:
            return True
        ok = self.msg.askokcancel(
            "Close PCSX2 first",
            "Changing a memory card while PCSX2 is running can corrupt it.\n\n"
            "Close PCSX2 (or eject the card in Settings > Memory Cards), then press OK.\n"
            "A safety copy of each card is made before every change.", parent=self)
        self.warned = ok
        return ok

    def _busy(self, on):
        self.configure(cursor="watch" if on else "")
        self.update_idletasks()

    def _modify(self, path: Path, func) -> bool:
        if not self._confirm_closed():
            return False
        self._busy(True)
        try:
            self._safety_copy(path)
            card = open_card(path)
            func(card)
            self._write(card, path)
            return True
        except ERRORS as ex:
            self.msg.showerror("Memory card", f"The card was not changed:\n\n{ex}", parent=self)
            return False
        finally:
            self._busy(False)

    def _need_card(self, pane):
        if not pane.path:
            self.msg.showinfo("Memory card", f"Pick a card on the {pane.label.lower()} first.",
                                parent=self)
            return False
        return True

    def _need_selection(self, pane):
        sel = pane.selected()
        if not sel:
            self.msg.showinfo("Memory card", f"Select one or more saves on the "
                                f"{pane.label.lower()} first.", parent=self)
        return sel

    def _ask_overwrite(self, names, existing):
        """Returns (chosen names, names to overwrite) or None if cancelled."""
        clash = [n for n in names if n in existing]
        overwrite = set()
        if clash:
            if len(clash) == 1:
                msg = f"'{clash[0]}' is already on the target card."
            else:
                msg = f"{len(clash)} saves are already on the target card:\n\n  " + \
                      "\n  ".join(clash[:12]) + ("\n  …" if len(clash) > 12 else "")
            ans = self.msg.askyesnocancel(
                "Already on card", msg + "\n\nYes = replace them with the copies being sent\n"
                "No = skip them\nCancel = stop", parent=self)
            if ans is None:
                return None
            if ans:
                overwrite = set(clash)
        chosen = [n for n in names if n not in clash or n in overwrite]
        return chosen, overwrite

    # ---- copy / move between the two cards ----------------------------------------------------
    def transfer(self, src: CardPane, dst: CardPane, move: bool):
        if not self._need_card(src) or not self._need_card(dst):
            return
        if same_file(src.path, dst.path):
            self.msg.showinfo("Memory card", "Both sides show the same card. "
                                "Pick a different card on one side.", parent=self)
            return
        names = self._need_selection(src)
        if not names:
            return
        verb = "Move" if move else "Copy"
        try:
            existing = set(open_card(dst.path).save_names())
        except ERRORS as ex:
            self.msg.showerror(verb, f"Cannot read {dst.path.name}: {ex}", parent=self)
            return
        picked = self._ask_overwrite(names, existing)
        if picked is None:
            return
        chosen, overwrite = picked
        if not chosen:
            self.status("Nothing to do: every selected save was skipped.")
            return
        if move and not self.msg.askokcancel(
                "Move saves", f"Move {len(chosen)} save(s) from {src.path.name} to {dst.path.name}?\n\n"
                "They are copied and verified first, then removed from the source card.",
                parent=self):
            return
        if not self._confirm_closed():
            return

        stage = "read"
        self._busy(True)
        try:
            src_card = open_card(src.path)
            dst_card = open_card(dst.path)
            stage = "backup"
            self._safety_copy(dst.path)
            if move:
                self._safety_copy(src.path)
            stage = "transfer"
            saves = transfer_saves(src_card, dst_card, chosen, overwrite, move)
            stage = "write-target"
            self._write(dst_card, dst.path)
            stage = "verify"
            verify_saves(open_card(dst.path), saves)        # check what actually reached the disk
            if move:
                stage = "write-source"
                self._write(src_card, src.path)
        except ERRORS as ex:
            if stage in ("read", "backup", "transfer"):
                note = "Neither card was changed."
            elif stage in ("write-target", "verify"):
                note = (f"{dst.path.name} may have been changed (its safety copy or any replaced "
                        f"saves are in the backup folder). {src.path.name} was not changed.")
            else:
                note = (f"The saves were copied to {dst.path.name}, but could not be removed "
                        f"from {src.path.name}. Nothing was lost.")
            self.msg.showerror(verb, f"{ex}\n\n{note}", parent=self)
            self.reload_all()
            return
        finally:
            self._busy(False)

        self.reload_all()
        dst.tree.selection_set([n for n in chosen if n in dst.rows])
        if chosen[0] in dst.rows:
            dst.tree.see(chosen[0])
        self.status(f"{'Moved' if move else 'Copied'} {len(chosen)} save(s): "
                    f"{src.path.name} → {dst.path.name}"
                    + (f"  ·  replaced {len(overwrite)}" if overwrite else ""))

    # ---- single-card save operations ---------------------------------------------------------
    def export_selected(self):
        pane = self.active
        if not self._need_card(pane):
            return
        names = self._need_selection(pane)
        if not names:
            return
        dest = self.ask_directory(parent=self, title="Export .psu files to…")
        if not dest:
            return
        try:
            card = open_card(pane.path)
            done = []
            for n in names:
                target = Path(dest) / f"{n}.psu"
                i = 2
                while target.exists():
                    target = Path(dest) / f"{n}_{i}.psu"
                    i += 1
                target.write_bytes(build_psu(card.get_save(n)))
                done.append(target.name)
        except ERRORS as ex:
            self.msg.showerror("Export", str(ex), parent=self)
            return
        self.status(f"Exported {len(done)} save(s) to {dest}")

    def import_psu(self):
        pane = self.active
        if not self._need_card(pane):
            return
        files = filedialog.askopenfilenames(parent=self, title="Choose .psu save files",
                                            filetypes=[("PS2 saves", "*.psu"), ("All files", "*")])
        if not files:
            return
        saves = []
        for f in files:
            try:
                saves.append(parse_psu(Path(f).read_bytes()))
            except ERRORS as ex:
                self.msg.showerror("Import", f"{Path(f).name}: {ex}", parent=self)
                return
        try:
            existing = set(open_card(pane.path).save_names())
        except ERRORS as ex:
            self.msg.showerror("Import", str(ex), parent=self)
            return
        picked = self._ask_overwrite([s.dirname for s in saves], existing)
        if picked is None:
            return
        chosen, overwrite = picked
        todo = [s for s in saves if s.dirname in chosen]
        if not todo:
            return

        def work(card):
            for s in todo:
                card.import_save(s, overwrite=s.dirname in overwrite)
            verify_saves(card, todo)

        if self._modify(pane.path, work):
            self.status(f"Imported {len(todo)} save(s) into {pane.path.name}")
        self.reload_all()

    def delete_selected(self):
        pane = self.active
        if not self._need_card(pane):
            return
        names = self._need_selection(pane)
        if not names:
            return
        shown = "\n  ".join(names[:15]) + ("\n  …" if len(names) > 15 else "")
        if not self.msg.askyesno("Delete saves", f"Permanently delete from {pane.path.name}:\n\n  "
                                   + shown + "\n\n" + self._kept_note(pane.path), parent=self):
            return

        def work(card):
            for n in names:
                card.delete_save(n)

        if self._modify(pane.path, work):
            self.status(f"Deleted {len(names)} save(s) from {pane.path.name}")
        self.reload_all()

    @staticmethod
    def _kept_note(path: Path):
        return ("The removed save folders are kept in the backup folder." if path.is_dir()
                else "A safety copy of the card is kept.")

    # ---- card file operations --------------------------------------------------------------------
    def _next_card_name(self, folder: Path):
        i = 1
        while (folder / f"Mcd{i:03d}.ps2").exists():
            i += 1
        return f"Mcd{i:03d}.ps2"

    def _ask_new_path(self, prompt, initial=None):
        pane = self.active
        d = self.card_dir() or (pane.path.parent if pane.path else None)
        if not d:
            self.msg.showinfo("Memory card", "PCSX2's memory card folder was not found.\n"
                                "Set it with 'Locations…' in the main window.", parent=self)
            return None
        d.mkdir(parents=True, exist_ok=True)
        name = simpledialog.askstring("Memory card", prompt, parent=self,
                                      initialvalue=initial or self._next_card_name(d))
        if not name:
            return None
        name = name.strip()
        if not Path(name).suffix:
            name += ".ps2"
        if os.sep in name or "/" in name or name.startswith(".") or name.startswith("_pcsx2_"):
            self.msg.showerror("Memory card", "Please enter a plain file name.", parent=self)
            return None
        target = d / name
        if target.exists():
            self.msg.showerror("Memory card", f"{name} already exists.", parent=self)
            return None
        return target

    def new_card(self):
        target = self._ask_new_path(f"Name for the new 8 MB card (opens on the "
                                    f"{self.active.label.lower()}):")
        if not target:
            return
        try:
            PS2Card.create_blank().save(target)
        except OSError as ex:
            self.msg.showerror("New card", str(ex), parent=self)
            return
        self.active.path = target
        self.refresh_card_lists()
        self.status(f"Created {target.name} (8 MB, formatted, empty). "
                    "Assign it to a slot in PCSX2 under Settings > Memory Cards.")

    def new_folder_card(self):
        target = self._ask_new_path(f"Name for the new folder memory card (opens on the "
                                    f"{self.active.label.lower()}):")
        if not target:
            return
        try:
            create_folder_card(target)
        except (CardError, OSError) as ex:
            self.msg.showerror("New folder card", str(ex), parent=self)
            return
        self.active.path = target
        self.refresh_card_lists()
        self.status(f"Created folder card {target.name} (formatted, empty). "
                    "In PCSX2 pick it under Settings > Memory Cards.")

    def duplicate_card(self):
        pane = self.active
        if not self._need_card(pane):
            return
        target = self._ask_new_path("Name for the copy:", f"{pane.path.stem}_copy{pane.path.suffix}")
        if not target:
            return
        try:
            if pane.path.is_dir():
                shutil.copytree(pane.path, target,
                                ignore=shutil.ignore_patterns("_pcsx2_deleted_*", "_pcsx2_tmp_*"))
            else:
                shutil.copy2(pane.path, target)
        except OSError as ex:
            self.msg.showerror("Duplicate", str(ex), parent=self)
            return
        other = self.right if pane is self.left else self.left
        other.path = target
        self.refresh_card_lists()
        self.status(f"Duplicated {pane.path.name} → {target.name} (shown on the {other.label.lower()})")

    def rename_card(self):
        pane = self.active
        if not self._need_card(pane) or not self._confirm_closed():
            return
        old = pane.path
        target = self._ask_new_path("New file name:", old.name)
        if not target:
            return
        try:
            old.rename(target)
        except OSError as ex:
            self.msg.showerror("Rename", str(ex), parent=self)
            return
        for p in (self.left, self.right):
            if p.path and p.path == old:
                p.path = target
        self.refresh_card_lists()
        self.status(f"Renamed {old.name} → {target.name}. Update the slot in PCSX2 if it used this card.")

    def delete_card(self):
        pane = self.active
        if not self._need_card(pane) or not self._confirm_closed():
            return
        path = pane.path
        kind = "folder card" if path.is_dir() else "card file"
        if not self.msg.askyesno(f"Delete {kind}", f"Delete {path.name} and ALL saves on it?\n"
                                   f"The {kind} is moved to the backup folder, not erased.", parent=self):
            return
        try:
            if path.is_dir():
                stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                dest = self.app.backup_dir / "_ps2_card_safety" / "deleted_cards" / f"{path.name}_{stamp}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
            else:
                self._safety_copy(path)
                path.unlink()
        except OSError as ex:
            self.msg.showerror("Delete", str(ex), parent=self)
            return
        for p in (self.left, self.right):
            if p.path == path:
                p.path = None
        self.refresh_card_lists()
        self.status(f"Deleted {path.name} (kept in the backup folder)")

    def open_folder(self):
        d = self.card_dir()
        if d and d.is_dir():
            self.app.open_folder(d)
        elif self.active.path:
            self.app.open_folder(self.active.path.parent)
