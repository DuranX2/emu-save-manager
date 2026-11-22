"""
ui_ps2console.py - the PS2 Memory Card Manager's console view.

Built on ui_console.ConsoleBase (the shared look, controls, menus and in-window questions).
What's PS2-specific lives here: each save's own 3D icon (rendered from its icon files, lit and
coloured by its icon.sys) in the grid, the selected save spinning on its own background
gradient, and the menus that drive PS2CardDialog's existing actions (transfer, delete,
export), so the same verification and safety copies apply.
"""
from __future__ import annotations

import hashlib
import math
import queue
import threading
import tkinter as tk
from collections import OrderedDict
from pathlib import Path

import ps2icon
from ui_console import ConsoleBase, ConsolePrompts, Overlay, Image, png_bytes  # noqa: F401

TILE_ICON, BIG_ICON = 88, 168
BIG_FRAMES = 36

_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()   # rendered PNGs, shared between windows
_CACHE_MAX = 3000
_CACHE_LOCK = threading.Lock()


def _cache_get(key):
    with _CACHE_LOCK:
        v = _CACHE.get(key)
        if v is not None:
            _CACHE.move_to_end(key)
        return v


def _cache_put(key, png):
    with _CACHE_LOCK:
        _CACHE[key] = png
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def frame_order(n: int) -> list:
    """0, n/2, n/4, 3n/4, … so a part-rendered animation still covers the whole turn."""
    order, seen, step = [], set(), n
    while step >= 1:
        for k in range(0, n, step):
            if k not in seen:
                seen.add(k)
                order.append(k)
        step //= 2
    order += [k for k in range(n) if k not in seen]
    return order


# ---------------------------------------------------------------------------
# Saves as shown here
# ---------------------------------------------------------------------------
class Item:
    def __init__(self, info, files: dict):
        self.dirname = info.dirname
        self.size_kb = info.size_kb
        self.modified = info.modified
        self.flags = [f for f, on in (("PS1 save", info.psx), ("protected", info.protected),
                                      ("hidden", info.hidden)) if on]
        sys_bytes = next((v for k, v in files.items() if k.lower() == "icon.sys"), b"")
        self.sys = ps2icon.parse_icon_sys(sys_bytes) if sys_bytes else None
        self.title1 = self.sys.title1 if self.sys else (info.title or info.dirname)
        self.title2 = self.sys.title2 if self.sys else ""
        self.files = sorted((k, len(v)) for k, v in files.items())
        icons = ps2icon.icon_files(files, self.sys)
        data = icons.get("normal", b"")
        self.icon = None
        if data:
            try:
                self.icon = ps2icon.parse_icon(data)
            except ps2icon.IconError:
                self.icon = None
        self.key = hashlib.sha1(data + sys_bytes).hexdigest() if data else None

    @property
    def name(self):
        return self.dirname

    @property
    def title(self):
        return " ".join(t for t in (self.title1, self.title2) if t)


def load_items(path) -> tuple:
    """(items, free KB or None, total KB or None) for one card file or folder card."""
    from ps2folder import open_card
    card = open_card(path)
    items = []
    for info in card.list_saves():
        try:
            s = card.get_save(info.dirname)
            files = {f.name: f.data for f in s.files}
        except Exception:  # noqa - a damaged save still shows, without an icon
            files = {}
        items.append(Item(info, files))
    items.sort(key=lambda it: it.title.lower() or it.dirname.lower())
    folder = getattr(card, "is_folder", False)
    free = None if folder else card.free_clusters()
    total = None if folder else card.n_alloc
    return items, free, total


# ---------------------------------------------------------------------------
# Icon rendering thread
# ---------------------------------------------------------------------------
class Renderer:
    def __init__(self):
        self.jobs = queue.PriorityQueue()
        self.done = queue.Queue()
        self.gen = 0
        self.seq = 0
        self.alive = True
        threading.Thread(target=self._run, daemon=True).start()

    def want(self, prio, item, kind, idx, gen=None):
        key = (item.key, kind, idx)
        png = _cache_get(key)
        if png is not None:
            self.done.put((key, png))
            return
        self.seq += 1
        self.jobs.put((prio, self.seq, key, item, kind, idx, self.gen if gen is None else gen))

    def _run(self):
        while self.alive:
            try:
                prio, _s, key, item, kind, idx, gen = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "big" and gen != self.gen:       # the selection moved on
                continue
            png = _cache_get(key)
            if png is None:
                try:
                    if kind == "tile":
                        img = ps2icon.render(item.icon, TILE_ICON, angle=-0.55, phase=0.0, sysinfo=item.sys)
                    else:
                        img = ps2icon.render(item.icon, BIG_ICON, angle=2 * math.pi * idx / BIG_FRAMES - 0.55,
                                             phase=idx / BIG_FRAMES, sysinfo=item.sys)
                    png = png_bytes(img)
                except Exception:  # noqa - one odd icon must not stop the others
                    png = b""
                _cache_put(key, png)
            self.done.put((key, png))

    def stop(self):
        self.alive = False


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------
class ConsoleView(ConsoleBase):
    HEADING = "Memory Card"
    SUBHEADING = "PS2"
    TILE_ICON = TILE_ICON

    def __init__(self, master, dialog):
        super().__init__(master)
        self.dialog = dialog
        self.space = [(None, None), (None, None)]
        self.renderer = Renderer() if Image is not None else None

    def on_stop(self):
        if self.renderer is not None:
            self.renderer.stop()

    # ---- data ------------------------------------------------------------------
    def panes(self):
        return (self.dialog.left, self.dialog.right)

    def reload(self):
        for i, pane in enumerate(self.panes()):
            self.items[i], self.space[i], self.error[i] = [], (None, None), ""
            if not pane.path:
                continue
            try:
                items, free, total = load_items(pane.path)
            except Exception as ex:  # noqa
                self.error[i] = f"Can't read {pane.path.name}: {ex}"
                continue
            self.items[i] = items
            self.space[i] = (free, total)
            if self.renderer is not None:
                for it in items:
                    if it.icon is not None:
                        self.renderer.want(10 if i == self.side else 20, it, "tile", 0)
        self.clamp()
        self._want_big()
        self.redraw(full=True)

    def _want_big(self):
        it = self.current()
        if self.renderer is None or it is None or it.icon is None:
            return
        self.renderer.gen += 1
        for n, idx in enumerate(frame_order(BIG_FRAMES)):
            self.renderer.want(n, it, "big", idx)

    def on_cursor(self):
        self._want_big()

    # ---- hooks -----------------------------------------------------------------
    def mark_key(self, it):
        return it.dirname

    def tab_label(self, side):
        pane = self.panes()[side]
        return f"Slot {side + 1} · {pane.path.name if pane.path else 'empty'}"

    def slot_present(self, side):
        return bool(self.panes()[side].path)

    def space_text(self, side):
        free, total = self.space[side]
        if free is not None and total:
            return f"{free:,} KB free of {total:,} KB"
        return "Folder card" if self.panes()[side].path else ""

    def tile_title(self, it):
        return it.title1 or it.dirname

    def tile_image(self, it):
        return self.photos.get((it.key, "tile", 0)) if it.key else None

    def marked_text(self, n):
        kb = sum(x.size_kb for x in self.items[self.side] if x.dirname in self.marks[self.side])
        return f"{n} marked · {kb:,} KB"

    def footer(self):
        return [("A", "Options"), ("X", "Mark"), ("Y", "Menu"), ("B", "Back"), ("LB/RB", "Slot"),
                ("☰", "List view")]

    def on_view(self):
        self.dialog.set_view("classic")

    def draw_info(self, it, box):
        c = self.canvas
        px0, py0, px1, py1 = box
        corners = it.sys.bg if it.sys else [(40, 40, 60)] * 4
        panel = self.panel_image((it.key, it.dirname), corners, px1 - px0, py1 - py0)
        if panel:
            c.create_image(px0, py0, image=panel, anchor="nw", tags="ui")
        c.create_image((px0 + px1) // 2, py0 + 18 + BIG_ICON // 2, tags=("ui", "big"))
        self._update_big()
        ty = py0 + BIG_ICON + 34
        tw = px1 - px0 - 32
        c.create_text(px0 + 16, ty, text=it.title1 or it.dirname, fill="#e9edff", font=self.f_title,
                      anchor="nw", width=tw, tags="ui")
        ty += self.f_title.metrics("linespace") + 2
        if it.title2:
            c.create_text(px0 + 16, ty, text=it.title2, fill="#e9edff", font=self.f_body, anchor="nw",
                          width=tw, tags="ui")
            ty += self.f_body.metrics("linespace") + 8
        self.text_rows(px0 + 16, ty, [f"{it.size_kb:,} KB",
                                      f"{it.modified:%Y-%m-%d  %H:%M}" if it.modified else "",
                                      it.dirname, " · ".join(it.flags)], tw)

    def _update_big(self):
        it = self.current()
        if it is None or not self.canvas.find_withtag("big"):
            return
        ph = None
        if it.key:
            idx = self.frame % BIG_FRAMES      # the newest rendered frame at or before this point
            for k in range(BIG_FRAMES):
                ph = self.photos.get((it.key, "big", (idx - k) % BIG_FRAMES))
                if ph:
                    break
            if ph is None:
                ph = self.photos.get((it.key, "tile", 0))
        if ph is None:
            ph = self.placeholder(BIG_ICON - 40)
        if ph:
            self.canvas.itemconfigure("big", image=ph)

    def on_tick(self):
        got = False
        if self.renderer is not None:
            for _ in range(12):
                try:
                    key, png = self.renderer.done.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.photos[key] = tk.PhotoImage(master=self.canvas, data=png) if png else None
                except tk.TclError:
                    self.photos[key] = None
                got = True
        if len(self.photos) > 1500:                      # keep memory in check on huge folder cards
            keep = {(it.key, "tile", 0) for side in self.items for it in side}
            cur = self.current()
            self.photos = {k: v for k, v in self.photos.items()
                           if k in keep or (cur is not None and k[0] == cur.key)}
        if not got:
            self._update_big()
        return got

    # ---- menus -------------------------------------------------------------------
    def _with_selection(self, names, func):
        pane = self.panes()[self.side]
        self.dialog.set_active(pane)
        pane.tree.selection_set([n for n in names if pane.tree.exists(n)])
        try:
            func()
        finally:
            self.marks[self.side].clear()
            self.reload()

    def save_menu(self):
        names = [it.dirname for it in self.selection()]
        if not names:
            return
        other = 1 - self.side
        op, mp = self.panes()[other], self.panes()[self.side]
        can_send = bool(op.path and mp.path and op.path != mp.path)
        what = f"{len(names)} saves" if len(names) > 1 else (self.current().title or names[0])
        opts = []
        if can_send:
            opts += [("copy", f"Copy to slot {other + 1}"), ("move", f"Move to slot {other + 1}")]
        opts += [("delete", "Delete"), ("export", "Export as .psu"), ("details", "Details"),
                 ("cancel", "Cancel")]

        def chosen(key):
            if key == "copy":
                self._with_selection(names, lambda: self.dialog.transfer(mp, op, False))
            elif key == "move":
                self._with_selection(names, lambda: self.dialog.transfer(mp, op, True))
            elif key == "delete":
                self._with_selection(names, self.dialog.delete_selected)
            elif key == "export":
                self._with_selection(names, self.dialog.export_selected)
            elif key == "details":
                self.details()
        self.show_overlay(what, "", opts, chosen)

    def details(self):
        it = self.current()
        if it is None:
            return
        lines = [f"Folder: {it.dirname}", f"Size: {it.size_kb:,} KB"]
        if it.modified:
            lines.append(f"Saved: {it.modified:%Y-%m-%d %H:%M} (card time)")
        if it.flags:
            lines.append("Flags: " + ", ".join(it.flags))
        lines.append("")
        lines += [f"{name}  ·  {size:,} bytes" for name, size in it.files[:14]]
        if len(it.files) > 14:
            lines.append(f"… and {len(it.files) - 14} more files")
        self.show_overlay(it.title or it.dirname, "\n".join(lines), [("ok", "Close")], lambda k: None)

    def card_menu(self, side):
        pane = self.panes()[side]
        found = self.dialog.found_cards()
        if not found:
            self.show_overlay(f"Slot {side + 1}", "No memory cards were found in PCSX2's folder. "
                              "Create one in the list view (F2 / Start).", [("ok", "OK")], lambda k: None)
            return
        opts = [(name, ("● " if pane.path and Path(p) == pane.path else "") + name) for name, p in found.items()]
        opts.append(("cancel", "Cancel"))

        def chosen(name):
            if name in found:
                pane.set_cards(found)
                pane.combo.set(name)
                pane.on_pick()
                self.side = side
                self.cursor[side] = 0
                self.reload()
        self.show_overlay(f"Choose the card for slot {side + 1}", "", opts, chosen)

    def main_menu(self):
        opts = [("slot1", "Choose card for slot 1…"), ("slot2", "Choose card for slot 2…"),
                ("refresh", "Refresh"), ("classic", "Switch to list view"), ("close", "Close manager"),
                ("cancel", "Cancel")]

        def chosen(key):
            if key == "slot1":
                self.card_menu(0)
            elif key == "slot2":
                self.card_menu(1)
            elif key == "refresh":
                self.dialog.refresh_card_lists()
                self.reload()
            elif key == "classic":
                self.dialog.set_view("classic")
            elif key == "close":
                self.dialog.destroy()
        self.show_overlay("Memory Card Manager", "", opts, chosen)
