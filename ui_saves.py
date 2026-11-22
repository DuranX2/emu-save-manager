"""
ui_saves.py - the "Current save data" list: saves grouped by game, with the game's name, the
save's own description and its icon, and each file's role underneath. A strip below shows the
selected save's banner or icon and details. Grouping comes from saveinfo.py, names from titledb.py.
"""
from __future__ import annotations

import io
import tkinter as tk
import tkinter.font  # noqa: F401 - tk.font
from datetime import datetime
from pathlib import PurePosixPath
from tkinter import ttk

import saveinfo

try:
    from PIL import Image
except Exception:  # noqa - the list still works without pictures
    Image = None

ROW_ICON = 32
KIND_ORDER = {"save": 0, "card": 1, "state": 2, "trophy": 3, "profile": 4}


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _when(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def icon_png(icon: saveinfo.Icon | None, box: int = ROW_ICON, wide: bool = False) -> bytes | None:
    """PNG bytes for an icon, fitted into box x box (or box*3 x box for banners), centred."""
    if icon is None or not icon.data:
        return None
    w_box, h_box = (box * 3, box) if wide else (box, box)
    if Image is None:
        if icon.kind == "png":
            return icon.data
        return saveinfo.png_bytes(icon.data, icon.width, icon.height)
    try:
        if icon.kind == "png":
            im = Image.open(io.BytesIO(icon.data)).convert("RGBA")
        else:
            im = Image.frombytes("RGBA", (icon.width, icon.height), icon.data)
        scale = min(w_box / im.width, h_box / im.height)
        if scale >= 2:                                 # small pixel art: keep it crisp
            k = int(scale)
            im = im.resize((im.width * k, im.height * k), Image.NEAREST)
        elif scale < 1:
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
        canvas = Image.new("RGBA", (w_box, h_box), (0, 0, 0, 0))
        canvas.paste(im, ((w_box - im.width) // 2, (h_box - im.height) // 2))
        out = io.BytesIO()
        canvas.save(out, "PNG")
        return out.getvalue()
    except Exception:  # noqa - a broken picture just isn't shown
        return None


def photo(master, png: bytes | None, box: int, wide=False):
    """A PhotoImage from icon_png() output; without Pillow, sized with zoom/subsample."""
    if not png:
        return None
    try:
        img = tk.PhotoImage(master=master, data=png)
    except tk.TclError:
        return None
    if Image is None:                                   # fit roughly without Pillow
        w, h = img.width(), img.height()
        tw = box * 3 if wide else box
        if w > tw or h > box:
            k = max(-(-w // tw), -(-h // box))
            img = img.subsample(k)
        elif w * 2 <= tw and h * 2 <= box:
            img = img.zoom(min(tw // w, box // h))
    return img


class SaveList(ttk.Frame):
    """Tree of saves. populate() fills it; selected_entries() returns the Entry objects picked."""

    def __init__(self, master, grouped=True, on_toggle=None):
        super().__init__(master)
        self.grouped = tk.BooleanVar(value=grouped)
        self.on_toggle = on_toggle
        self.entries = {}            # iid -> Entry (file rows)
        self.group_of = {}           # iid -> group iid
        self.groups = {}             # group iid -> (SaveGroup, [Entry])
        self.inner = {}              # iid -> (group iid, Inner)
        self._images = []
        self._detail_images = []
        style = ttk.Style(self)
        line = max(20, tk.font.nametofont("TkDefaultFont").metrics("linespace") + 6)
        style.configure("Saves.Treeview", rowheight=max(ROW_ICON + 4, line))
        style.configure("SavesFlat.Treeview", rowheight=line)
        cols = ("info", "location", "size", "modified")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", height=8,
                                 selectmode="extended", style="Saves.Treeview")
        self.tree.heading("#0", text="Save")
        self.tree.column("#0", width=250, minwidth=160, stretch=True)
        for c, text, w, anchor in (("info", "Description / file", 230, "w"), ("location", "Location", 100, "w"),
                                   ("size", "Size", 72, "e"), ("modified", "Modified", 122, "w")):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=anchor, stretch=c == "info")
        self.tree.tag_configure("inner", foreground="#555")
        self.tree.tag_configure("unknown", foreground="#666")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # the selected save: banner/icon and two lines of text
        self.detail = ttk.Frame(self)
        self.detail.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.d_pic = ttk.Label(self.detail)
        self.d_pic.pack(side="left", padx=(0, 8))
        txt = ttk.Frame(self.detail)
        txt.pack(side="left", fill="x", expand=True)
        self.d_title = ttk.Label(txt, font=("TkDefaultFont", 10, "bold"), anchor="w")
        self.d_title.pack(fill="x")
        self.d_info = ttk.Label(txt, anchor="w", foreground="#555")
        self.d_info.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    # ---- filling -------------------------------------------------------------
    def populate(self, emu: str, located: list, titles=None):
        """located: [(label, base Path, [Entry, ...])]."""
        tree = self.tree
        tree.delete(*tree.get_children())
        self.entries, self.group_of, self.groups, self.inner = {}, {}, {}, {}
        self._images = []
        self._show_detail(None)
        by_key = {}
        flat_list = []
        for label, base, entries in located:
            for e in entries:
                by_key[(label, e.rel.as_posix())] = e
                flat_list.append(e)
        try:
            groups, leftover = saveinfo.identify(
                emu, [(label, base, [e.rel.as_posix() for e in entries]) for label, base, entries in located])
        except Exception as ex:  # noqa - never lose the file list over a parsing problem
            print("Save identification failed:", ex)
            groups, leftover = [], [(e.location, e.rel.as_posix()) for e in flat_list]
        if titles is not None:
            for g in groups:                 # remember names seen in saves (for states, other machines)
                if g.title and not g.needs_title and g.id_kind in ("gc", "wii", "psp", "ps3"):
                    titles.learn(g.id_kind, g.game_id, g.title)
            saveinfo.apply_titles(groups, titles)
        self.last_groups = groups
        n = 0
        if not self.grouped.get():
            tree.configure(style="SavesFlat.Treeview")
            title_of = {}
            for g in groups:
                for label, rel, role in g.files:
                    title_of[(label, rel)] = (g.label(), role)
            for e in flat_list:
                iid = f"f{n}"
                n += 1
                self.entries[iid] = e
                t, role = title_of.get((e.location, e.rel.as_posix()), ("", ""))
                tree.insert("", "end", iid=iid, text=t, values=(
                    e.rel.as_posix(), e.location, human_size(e.size), _when(e.mtime)))
            return
        tree.configure(style="Saves.Treeview")
        order = sorted(groups, key=lambda g: (g.label().lower(), KIND_ORDER.get(g.kind, 9), g.detail.lower()))
        for gi, g in enumerate(order):
            files = [(by_key[(lab, rel)], role) for lab, rel, role in g.files if (lab, rel) in by_key]
            if not files:
                continue
            giid = f"g{gi}"
            img = photo(self, icon_png(g.icon), ROW_ICON)
            if img:
                self._images.append(img)
            locs = sorted({e.location for e, _r in files})
            size = sum(e.size for e, _r in files)
            newest = max(e.mtime for e, _r in files)
            info = g.detail or ("" if g.title else g.game_id)
            tags = () if g.title else ("unknown",)
            kw = {"image": img} if img else {}
            tree.insert("", "end", iid=giid, text=g.label(), values=(info, ", ".join(locs),
                                                                     human_size(size), _when(newest)),
                        tags=tags, **kw)
            self.groups[giid] = (g, [e for e, _r in files])
            for e, role in sorted(files, key=lambda t: t[0].rel.as_posix().lower()):
                iid = f"f{n}"
                n += 1
                self.entries[iid] = e
                self.group_of[iid] = giid
                tree.insert(giid, "end", iid=iid, text=role, values=(
                    e.rel.as_posix(), e.location, human_size(e.size), _when(e.mtime)))
            for k, inner in enumerate(g.inner):
                iid = f"{giid}i{k}"
                self.inner[iid] = (giid, inner)
                small = photo(self, icon_png(inner.icon), ROW_ICON)
                if small:
                    self._images.append(small)
                bits = [inner.detail if inner.detail != inner.label() else "", inner.game_id,
                        f"{inner.blocks} block{'s' if inner.blocks != 1 else ''}" if inner.blocks else ""]
                tree.insert(giid, "end", iid=iid, text=inner.label(),
                            values=(" · ".join(b for b in bits if b), "", "", ""), tags=("inner",),
                            **({"image": small} if small else {}))
        for label, rel in leftover:
            e = by_key.get((label, rel))
            if e is None:
                continue
            iid = f"f{n}"
            n += 1
            self.entries[iid] = e
            tree.insert("", "end", iid=iid, text=PurePosixPath(rel).name, values=(
                rel, e.location, human_size(e.size), _when(e.mtime)), tags=("unknown",))

    # ---- selection -----------------------------------------------------------
    def selected_entries(self) -> list:
        out, seen = [], set()

        def add(e):
            if id(e) not in seen:
                seen.add(id(e))
                out.append(e)
        for iid in self.tree.selection():
            if iid in self.entries:
                add(self.entries[iid])
            elif iid in self.groups:
                for e in self.groups[iid][1]:
                    add(e)
            elif iid in self.inner:                     # a save inside a card: the card file
                for e in self.groups[self.inner[iid][0]][1]:
                    add(e)
        return out

    def all_entries(self) -> list:
        return list(self.entries.values())

    def summary(self) -> str:
        files = len(self.entries)
        total = sum(e.size for e in self.entries.values())
        games = len({g.label() for g, _e in self.groups.values()})
        s = f"{files} file(s), {human_size(total)}"
        if self.grouped.get() and self.groups:
            s += f" · {len(self.groups)} save(s) from {games} game(s)"
        return s

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        self._show_detail(sel[0] if len(sel) == 1 else None)

    def _show_detail(self, iid):
        self._detail_images = []
        if iid is None:
            self.d_pic.configure(image="", text="")
            self.d_title.configure(text="")
            self.d_info.configure(text="")
            return
        g = None
        if iid in self.groups:
            g = self.groups[iid][0]
        elif iid in self.group_of:
            g = self.groups[self.group_of[iid]][0]
        if iid in self.inner:
            giid, inner = self.inner[iid]
            img = photo(self, icon_png(inner.icon), ROW_ICON)
            self.d_title.configure(text=inner.label())
            self.d_info.configure(text=" · ".join(b for b in (
                inner.detail if inner.detail != inner.label() else "", inner.game_id,
                f"in {self.groups[giid][0].label()}") if b))
        elif g is not None:
            img = None
            if g.banner is not None:
                img = photo(self, icon_png(g.banner, ROW_ICON, wide=True), ROW_ICON, wide=True)
            if img is None:
                img = photo(self, icon_png(g.icon), ROW_ICON)
            files = self.groups[self.group_of.get(iid, iid)][1]
            self.d_title.configure(text=g.label() + (f" — {g.detail}" if g.detail else ""))
            bits = [g.system, f"Game ID {g.game_id}" if g.game_id else "",
                    f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "",
                    f"{len(g.inner)} save{'s' if len(g.inner) != 1 else ''} on the card" if g.inner else ""]
            if iid in self.entries:
                e = self.entries[iid]
                bits.append(f"This file: {self.tree.item(iid, 'text')} ({e.rel.as_posix()})")
            self.d_info.configure(text=" · ".join(b for b in bits if b))
        else:
            e = self.entries.get(iid)
            img = None
            self.d_title.configure(text=PurePosixPath(e.rel.as_posix()).name if e else "")
            self.d_info.configure(text=f"{e.location}: {e.rel.as_posix()} (not recognised)" if e else "")
        if img:
            self._detail_images.append(img)
            self.d_pic.configure(image=img)
        else:
            self.d_pic.configure(image="")
