"""
ui_ps2console.py - the PS2 Memory Card Manager's console view.

A full-window, controller-friendly view of the same two cards as the list view: each save's own
3D icon (rendered from its icon files, lit and coloured by its icon.sys) in a grid, the selected
save spinning on its own background gradient, and menus and confirmations drawn inside the
window. The look is an original design in the spirit of a console's memory card screen: no
console artwork, fonts or sounds are used.

Every change goes through PS2CardDialog's existing actions (transfer, delete, export), so the
same verification and safety copies apply; their questions are answered here (see
ConsolePrompts) instead of in pop-up message boxes.
"""
from __future__ import annotations

import hashlib
import io
import math
import queue
import random
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import OrderedDict
from pathlib import Path

import gamepad
import ps2icon

try:
    from PIL import Image, ImageDraw, ImageFilter
except Exception:  # noqa
    Image = None

TILE_ICON, BIG_ICON = 88, 168
BIG_FRAMES = 36
TICK_MS = 66
BG_TOP, BG_BOTTOM = (10, 14, 40), (4, 5, 16)
TEXT, DIM, ACCENT, MARK = "#e9edff", "#9aa5d4", "#7fd4ff", "#ffd479"

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


def _png(img) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


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
    def title(self):
        return " ".join(t for t in (self.title1, self.title2) if t)


def load_items(path) -> tuple:
    """(items, free KB or None, card label) for one card file or folder card."""
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
                    png = _png(img)
                except Exception:  # noqa - one odd icon must not stop the others
                    png = b""
                _cache_put(key, png)
            self.done.put((key, png))

    def stop(self):
        self.alive = False


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------
class ConsoleView(tk.Frame):
    def __init__(self, master, dialog):
        super().__init__(master, bg="#05060f", highlightthickness=0)
        self.dialog = dialog
        self.canvas = tk.Canvas(self, bg="#05060f", highlightthickness=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)
        self.side = 0
        self.items = [[], []]
        self.cursor = [0, 0]
        self.scroll = [0, 0]
        self.marks = [set(), set()]
        self.space = [(None, None), (None, None)]
        self.error = ["", ""]
        self.photos = {}                   # (key, kind, idx) -> PhotoImage (or None if it failed)
        self.static = {}                   # backgrounds, glyphs, panels
        self.overlay = None
        self.toast_text, self.toast_until = "", 0.0
        self.frame = 0
        self.renderer = Renderer() if Image is not None else None
        self.nav = None
        self.running = False
        self.prompts = ConsolePrompts(self)
        self._layout_size = (0, 0)
        self._bokeh = [[random.random(), random.random(), random.uniform(0.6, 1.4), random.uniform(-1, 1)]
                       for _ in range(12)]
        self._fonts()
        self.canvas.bind("<Configure>", lambda e: self.redraw(full=True))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-Button-1>", self._double)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self.act("up"))
        self.canvas.bind("<Button-5>", lambda e: self.act("down"))
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ---- set-up --------------------------------------------------------------
    def _fonts(self):
        fams = set(tkfont.families(self))
        fam = next((f for f in ("Segoe UI", "Noto Sans", "DejaVu Sans", "Helvetica") if f in fams), "TkDefaultFont")
        self.f_head = tkfont.Font(self, family=fam, size=18)
        self.f_tab = tkfont.Font(self, family=fam, size=12)
        self.f_title = tkfont.Font(self, family=fam, size=15, weight="bold")
        self.f_body = tkfont.Font(self, family=fam, size=11)
        self.f_small = tkfont.Font(self, family=fam, size=10)
        self.f_glyph = tkfont.Font(self, family=fam, size=10, weight="bold")

    def start(self):
        self.running = True
        if self.nav is None:
            self.nav = gamepad.Navigator(self.canvas, self.act)
        self.nav.enabled = True
        self.canvas.focus_set()
        self.reload()
        self.after(TICK_MS, self._tick)

    def stop(self):
        self.running = False
        if self.nav is not None:
            self.nav.enabled = False

    def _on_destroy(self, event):
        if event.widget is self:
            self.running = False
            if self.nav is not None:
                self.nav.stop()
            if self.renderer is not None:
                self.renderer.stop()
            self.prompts.cancel_all()

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
            names = {it.dirname for it in items}
            self.marks[i] &= names
            self.cursor[i] = min(self.cursor[i], max(0, len(items) - 1))
            if self.renderer is not None:
                for it in items:
                    if it.icon is not None:
                        self.renderer.want(10 if i == self.side else 20, it, "tile", 0)
        self._want_big()
        self.redraw(full=True)

    def current(self):
        items = self.items[self.side]
        return items[self.cursor[self.side]] if items else None

    def _want_big(self):
        it = self.current()
        if self.renderer is None or it is None or it.icon is None:
            return
        self.renderer.gen += 1
        for n, idx in enumerate(frame_order(BIG_FRAMES)):
            self.renderer.want(n, it, "big", idx)

    # ---- actions -----------------------------------------------------------------
    def act(self, action):
        if not self.running:
            return
        if self.overlay is not None:
            self.overlay.act(action)
            return
        items = self.items[self.side]
        cols = self._cols()
        c = self.cursor[self.side]
        if action in ("left", "right", "up", "down") and items:
            if action == "left":
                if c % cols == 0:
                    self._switch(0)
                    return
                c -= 1
            elif action == "right":
                if c % cols == cols - 1 or c == len(items) - 1:
                    self._switch(1)
                    return
                c += 1
            elif action == "up":
                c = c - cols if c - cols >= 0 else c
            elif action == "down":
                if c + cols < len(items):
                    c += cols
                elif c // cols < (len(items) - 1) // cols:      # a shorter last row: go to its end
                    c = len(items) - 1
            self._move(c)
        elif action in ("left", "right") and not items:
            self._switch(0 if action == "left" else 1)
        elif action in ("prev", "next"):
            self._switch(0 if action == "prev" else 1)
        elif action == "mark" and items:
            name = items[c].dirname
            self.marks[self.side] ^= {name}
            self.redraw()
        elif action == "ok":
            if items:
                self.save_menu()
            else:
                self.card_menu(self.side)
        elif action == "menu":
            self.main_menu()
        elif action == "back":
            if self.marks[self.side]:
                self.marks[self.side].clear()
                self.redraw()
            else:
                self.main_menu()
        elif action == "view":
            self.dialog.set_view("classic")

    def _move(self, c):
        if c != self.cursor[self.side]:
            self.cursor[self.side] = c
            self._want_big()
            self.redraw()

    def _switch(self, side):
        if side != self.side:
            self.side = side
            self._want_big()
            self.redraw()

    def selection(self):
        """Marked saves on the active side, or the one under the cursor."""
        items = self.items[self.side]
        marked = [it.dirname for it in items if it.dirname in self.marks[self.side]]
        if marked:
            return marked
        it = self.current()
        return [it.dirname] if it else []

    def _with_selection(self, names, func):
        pane = self.panes()[self.side]
        self.dialog.set_active(pane)
        pane.tree.selection_set([n for n in names if pane.tree.exists(n)])
        try:
            func()
        finally:
            self.marks[self.side].clear()
            self.reload()

    # ---- menus ---------------------------------------------------------------------
    def save_menu(self):
        names = self.selection()
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

    def show_overlay(self, title, message, options, on_choose, cancel_key="cancel"):
        self.overlay = Overlay(self, title, message, options, on_choose, cancel_key)
        self.redraw()

    def close_overlay(self):
        self.overlay = None
        self.redraw()

    def toast(self, text, seconds=4.0):
        self.toast_text, self.toast_until = text, time.monotonic() + seconds
        self.redraw()

    # ---- mouse ---------------------------------------------------------------------
    def _click(self, e):
        self.canvas.focus_set()
        if self.overlay is not None:
            self.overlay.click(e.x, e.y)
            return
        for side, (x0, y0, x1, y1) in enumerate(self._tab_boxes()):
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                if side == self.side:
                    self.card_menu(side)
                else:
                    self._switch(side)
                return
        idx = self._tile_at(e.x, e.y)
        if idx is not None:
            self._move(idx)

    def _double(self, e):
        if self.overlay is None and self._tile_at(e.x, e.y) is not None:
            self.save_menu()

    def _wheel(self, e):
        self.act("up" if e.delta > 0 else "down")

    # ---- layout --------------------------------------------------------------------
    def _geom(self):
        w = max(640, self.canvas.winfo_width())
        h = max(400, self.canvas.winfo_height())
        head, foot = 64, 46
        split = int(w * 0.62)
        return w, h, head, foot, split

    def _cols(self):
        w, h, head, foot, split = self._geom()
        return max(2, (split - 40) // 132)

    def _tile_box(self, idx):
        w, h, head, foot, split = self._geom()
        cols = self._cols()
        tw, th = (split - 40) // cols, 150
        row, col = divmod(idx, cols)
        x0 = 24 + col * tw
        y0 = head + 16 + (row - self.scroll[self.side]) * th
        return x0, y0, x0 + tw, y0 + th

    def _tile_at(self, x, y):
        for i in range(len(self.items[self.side])):
            x0, y0, x1, y1 = self._tile_box(i)
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i
        return None

    def _tab_boxes(self):
        w, h, head, foot, split = self._geom()
        x = 270
        boxes = []
        for side in (0, 1):
            label = self._tab_label(side)
            tw = self.f_tab.measure(label) + 30
            boxes.append((x, 14, x + tw, head - 10))
            x += tw + 12
        return boxes

    def _tab_label(self, side):
        pane = self.panes()[side]
        name = pane.path.name if pane.path else "empty"
        return f"Slot {side + 1} · {name}"

    def _keep_visible(self):
        w, h, head, foot, split = self._geom()
        rows_vis = max(1, (h - head - foot - 24) // 150)
        row = self.cursor[self.side] // self._cols()
        s = self.scroll[self.side]
        if row < s:
            s = row
        elif row >= s + rows_vis:
            s = row - rows_vis + 1
        self.scroll[self.side] = max(0, s)

    # ---- drawing -------------------------------------------------------------------
    def _photo(self, key, png=None, img=None):
        ph = self.static.get(key)
        if ph is None:
            if img is not None:
                png = _png(img)
            ph = tk.PhotoImage(master=self.canvas, data=png) if png else None
            self.static[key] = ph
        return ph

    def _background(self, w, h):
        key = ("bg", w, h)
        if key not in self.static and Image is not None:
            top = Image.new("RGB", (1, 2))
            top.putdata([BG_TOP, BG_BOTTOM])
            img = top.resize((max(1, w // 4), max(1, h // 4)), Image.BILINEAR)
            glow = Image.new("L", img.size, 0)
            gw, gh = img.size
            ImageDraw.Draw(glow).ellipse((-gw * 0.2, -gh * 0.6, gw * 0.9, gh * 0.7), fill=60)
            glow = glow.filter(ImageFilter.GaussianBlur(max(2, gw // 10)))
            tint = Image.new("RGB", img.size, (40, 70, 150))
            img = Image.composite(tint, img, glow).resize((w, h), Image.BILINEAR)
            self.static = {k: v for k, v in self.static.items() if k[0] != "bg"}
            self._photo(key, img=img)
        return self.static.get(key)

    def _bokeh_img(self, r):
        key = ("bokeh", r)
        if key not in self.static and Image is not None:
            size = r * 4
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((r, r, 3 * r, 3 * r), fill=(150, 190, 255, 38))
            im = im.filter(ImageFilter.GaussianBlur(r * 0.6))
            self._photo(key, img=im)
        return self.static.get(key)

    def _panel(self, it, w, h):
        key = ("panel", it.key if it else None, it.dirname if it else "", w, h)
        if key not in self.static and Image is not None:
            grad = ps2icon.gradient(it.sys if it else None, w, h).convert("RGBA")
            dark = Image.new("RGBA", (w, h), (0, 0, 0, 90))
            grad.alpha_composite(dark)
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=18, fill=235)
            grad.putalpha(mask)
            self.static = {k: v for k, v in self.static.items() if k[0] != "panel"}
            self._photo(key, img=grad)
        return self.static.get(key)

    def _placeholder(self, size):
        key = ("placeholder", size)
        if key not in self.static and Image is not None:
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            m = size // 5
            d.rounded_rectangle((m, m // 2, size - m, size - m // 2), radius=size // 10,
                                fill=(70, 80, 120, 255), outline=(150, 165, 220, 255), width=2)
            d.rectangle((size // 2 - m, m, size // 2 + m, m + m), fill=(40, 46, 70, 255))
            self._photo(key, img=im)
        return self.static.get(key)

    def _glyph(self, x, y, letters, label):
        """Button glyphs ("A", or "LB/RB" as two pills) followed by what they do."""
        c = self.canvas
        r = 11
        for letter in letters.split("/"):
            tw = self.f_glyph.measure(letter)
            if len(letter) == 1 or tw <= 12:
                c.create_oval(x - r, y - r, x + r, y + r, outline=ACCENT, width=2, tags="ui")
                c.create_text(x, y, text=letter, fill=ACCENT, font=self.f_glyph, tags="ui")
                x += 2 * r + 4
            else:
                half = tw / 2 + 6
                cx = x - r + half
                pts = [cx - half + r, y - r, cx + half - r, y - r, cx + half, y - r, cx + half, y,
                       cx + half, y + r, cx + half - r, y + r, cx - half + r, y + r, cx - half, y + r,
                       cx - half, y, cx - half, y - r]
                c.create_polygon(pts, smooth=True, outline=ACCENT, fill="", width=2, tags="ui")
                c.create_text(cx, y, text=letter, fill=ACCENT, font=self.f_glyph, tags="ui")
                x += 2 * half + 4
        c.create_text(x - r + 2, y, text=label, fill=TEXT, font=self.f_small, anchor="w", tags="ui")
        return x - r + 2 + self.f_small.measure(label) + 22

    def redraw(self, full=False):
        if not self.running and not full:
            return
        c = self.canvas
        w, h, head, foot, split = self._geom()
        if full or self._layout_size != (w, h):
            self._layout_size = (w, h)
            c.delete("all")
            bg = self._background(w, h)
            if bg:
                c.create_image(0, 0, image=bg, anchor="nw", tags="bg")
            self._draw_bokeh(create=True)
        c.delete("ui")
        self._keep_visible()
        # header
        c.create_text(24, head // 2, text="Memory Card", fill=TEXT, font=self.f_head, anchor="w", tags="ui")
        c.create_text(24 + self.f_head.measure("Memory Card") + 8, head // 2 + 4, text="PS2",
                      fill=DIM, font=self.f_small, anchor="w", tags="ui")
        for side, (x0, y0, x1, y1) in enumerate(self._tab_boxes()):
            on = side == self.side
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2 - 2, text=self._tab_label(side),
                          fill=TEXT if on else DIM, font=self.f_tab, tags="ui")
            if on:
                c.create_line(x0 + 8, y1, x1 - 8, y1, fill=ACCENT, width=3, tags="ui")
        free, total = self.space[self.side]
        if free is not None and total:
            txt = f"{free:,} KB free of {total:,} KB"
        elif self.panes()[self.side].path:
            txt = "Folder card"
        else:
            txt = ""
        c.create_text(w - 24, head // 2, text=txt, fill=DIM, font=self.f_small, anchor="e", tags="ui")
        c.create_line(24, head, w - 24, head, fill="#2a3360", tags="ui")
        # grid
        items = self.items[self.side]
        c.create_rectangle(0, head + 1, split, h - foot - 1, outline="", fill="", tags="ui")
        if self.error[self.side]:
            c.create_text(split // 2, h // 2, text=self.error[self.side], fill=MARK, width=split - 80,
                          font=self.f_body, tags="ui")
        elif not self.panes()[self.side].path:
            c.create_text(split // 2, h // 2, text="No card in this slot.\nPress Ⓐ or Enter to choose one.",
                          fill=DIM, justify="center", font=self.f_body, tags="ui")
        elif not items:
            c.create_text(split // 2, h // 2, text="This card has no saves.", fill=DIM, font=self.f_body, tags="ui")
        for i, it in enumerate(items):
            x0, y0, x1, y1 = self._tile_box(i)
            if y1 < head or y0 > h - foot:
                continue
            cx = (x0 + x1) // 2
            sel = i == self.cursor[self.side]
            if sel:
                c.create_rectangle(x0 + 6, y0 + 2, x1 - 6, y1 - 4, outline=ACCENT, width=2, tags="ui")
            ph = self.photos.get((it.key, "tile", 0)) if it.key else None
            if ph is None:
                ph = self._placeholder(TILE_ICON - 16)
            if ph:
                c.create_image(cx, y0 + 12 + TILE_ICON // 2, image=ph, tags="ui")
            title = it.title1 or it.dirname
            while self.f_small.measure(title) > (x1 - x0 - 14) and len(title) > 3:
                title = title[:-2]
            if title != (it.title1 or it.dirname):
                title = title.rstrip() + "…"
            c.create_text(cx, y0 + TILE_ICON + 24, text=title, fill=TEXT if sel else DIM,
                          font=self.f_small, tags="ui")
            if it.dirname in self.marks[self.side]:
                c.create_oval(x1 - 34, y0 + 8, x1 - 14, y0 + 28, fill=MARK, outline="", tags="ui")
                c.create_text(x1 - 24, y0 + 18, text="✓", fill="#1a1a1a", font=self.f_glyph, tags="ui")
        # info panel
        it = self.current()
        px0, py0, px1, py1 = split + 8, head + 16, w - 24, h - foot - 12
        if it is not None and px1 - px0 > 120:
            panel = self._panel(it, px1 - px0, py1 - py0)
            if panel:
                c.create_image(px0, py0, image=panel, anchor="nw", tags="ui")
            self._big_item = c.create_image((px0 + px1) // 2, py0 + 18 + BIG_ICON // 2, tags=("ui", "big"))
            self._update_big()
            ty = py0 + BIG_ICON + 34
            tw = px1 - px0 - 32
            c.create_text(px0 + 16, ty, text=it.title1 or it.dirname, fill=TEXT, font=self.f_title,
                          anchor="nw", width=tw, tags="ui")
            ty += self.f_title.metrics("linespace") + 2
            if it.title2:
                c.create_text(px0 + 16, ty, text=it.title2, fill=TEXT, font=self.f_body, anchor="nw",
                              width=tw, tags="ui")
                ty += self.f_body.metrics("linespace") + 8
            rows = [f"{it.size_kb:,} KB", f"{it.modified:%Y-%m-%d  %H:%M}" if it.modified else "",
                    it.dirname, " · ".join(it.flags)]
            for r in rows:
                if r:
                    c.create_text(px0 + 16, ty, text=r, fill="#d5dbf7", font=self.f_small, anchor="nw",
                                  width=tw, tags="ui")
                    ty += self.f_small.metrics("linespace") + 2
            n = len(self.marks[self.side])
            if n:
                kb = sum(x.size_kb for x in items if x.dirname in self.marks[self.side])
                c.create_text(px0 + 16, py1 - 16, text=f"{n} marked · {kb:,} KB", fill=MARK,
                              font=self.f_body, anchor="sw", tags="ui")
        # footer
        c.create_line(24, h - foot, w - 24, h - foot, fill="#2a3360", tags="ui")
        x, y = 36, h - foot // 2
        for letter, label in (("A", "Options"), ("X", "Mark"), ("Y", "Menu"), ("B", "Back"),
                              ("LB/RB", "Slot"), ("☰", "List view")):
            x = self._glyph(x, y, letter, label)
        c.create_text(w - 24, y, text="Keys: arrows · Enter · X · Esc · Q/E · F2", fill=DIM,
                      font=self.f_small, anchor="e", tags="ui")
        if self.toast_text and time.monotonic() < self.toast_until:
            tw = self.f_body.measure(self.toast_text) + 40
            c.create_rectangle(w // 2 - tw // 2, h - foot - 54, w // 2 + tw // 2, h - foot - 18,
                               fill="#1b2450", outline=ACCENT, tags="ui")
            c.create_text(w // 2, h - foot - 36, text=self.toast_text, fill=TEXT, font=self.f_body, tags="ui")
        if self.overlay is not None:
            self.overlay.draw()

    def _update_big(self):
        it = self.current()
        if it is None or not self.canvas.find_withtag("big"):
            return
        ph = None
        if it.key:
            # the newest rendered frame at or before this point in the turn
            idx = self.frame % BIG_FRAMES
            for k in range(BIG_FRAMES):
                ph = self.photos.get((it.key, "big", (idx - k) % BIG_FRAMES))
                if ph:
                    break
            if ph is None:
                ph = self.photos.get((it.key, "tile", 0))
        if ph is None:
            ph = self._placeholder(BIG_ICON - 40)
        if ph:
            self.canvas.itemconfigure("big", image=ph)

    def _draw_bokeh(self, create=False):
        c = self.canvas
        w, h, *_ = self._geom()
        if create:
            c.delete("bokeh")
            for k, b in enumerate(self._bokeh):
                img = self._bokeh_img(int(18 * b[2]))
                if img:
                    c.create_image(b[0] * w, b[1] * h, image=img, tags=("bokeh", f"bokeh{k}"))
            c.tag_raise("bokeh", "bg")
            return
        t = time.monotonic()
        for k, b in enumerate(self._bokeh):
            x = (b[0] + 0.004 * math.sin(t * 0.21 + k)) * w
            y = ((b[1] - t * 0.004 * b[2]) % 1.0) * h
            c.coords(f"bokeh{k}", x, y)

    def _tick(self):
        if not self.running:
            return
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
        self.frame += 1
        if got and self.overlay is None:
            self.redraw()
        else:
            self._update_big()
        self._draw_bokeh()
        if self.toast_text and time.monotonic() >= self.toast_until:
            self.toast_text = ""
            self.redraw()
        self.after(TICK_MS, self._tick)


class Overlay:
    """A menu or question drawn over the view; navigable by controller, keyboard or mouse."""

    def __init__(self, view, title, message, options, on_choose, cancel_key):
        self.view = view
        self.title, self.message, self.options = title, message, options
        self.on_choose, self.cancel_key = on_choose, cancel_key
        self.index = 0
        self.boxes = []

    def act(self, action):
        if action == "up":
            self.index = (self.index - 1) % len(self.options)
        elif action == "down":
            self.index = (self.index + 1) % len(self.options)
        elif action == "ok":
            self.choose(self.options[self.index][0])
            return
        elif action == "back":
            keys = [k for k, _l in self.options]
            self.choose(self.cancel_key if self.cancel_key in keys else keys[-1])
            return
        self.view.redraw()

    def choose(self, key):
        self.view.close_overlay()
        self.on_choose(key)

    def click(self, x, y):
        for i, (x0, y0, x1, y1) in enumerate(self.boxes):
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.index = i
                self.choose(self.options[i][0])
                return

    def draw(self):
        v, c = self.view, self.view.canvas
        w, h, *_ = v._geom()
        bw = min(560, w - 80)
        msg_h = 0
        if self.message:
            tmp = c.create_text(0, 0, text=self.message, font=v.f_body, width=bw - 48, anchor="nw")
            bb = c.bbox(tmp)
            c.delete(tmp)
            msg_h = (bb[3] - bb[1]) + 16 if bb else 0
        row = v.f_body.metrics("linespace") + 14
        bh = 70 + msg_h + row * len(self.options) + 16
        bh = min(bh, h - 40)
        x0, y0 = (w - bw) // 2, (h - bh) // 2
        c.create_rectangle(0, 0, w, h, fill="#000000", stipple="gray50", outline="", tags="ui")
        c.create_rectangle(x0, y0, x0 + bw, y0 + bh, fill="#121a3d", outline=ACCENT, width=2, tags="ui")
        c.create_text(x0 + 24, y0 + 20, text=self.title, fill=TEXT, font=v.f_title, anchor="nw",
                      width=bw - 48, tags="ui")
        y = y0 + 58
        if self.message:
            c.create_text(x0 + 24, y, text=self.message, fill="#d5dbf7", font=v.f_body, anchor="nw",
                          width=bw - 48, tags="ui")
            y += msg_h
        self.boxes = []
        for i, (_k, label) in enumerate(self.options):
            box = (x0 + 16, y, x0 + bw - 16, y + row - 4)
            self.boxes.append(box)
            if i == self.index:
                c.create_rectangle(*box, fill="#24336e", outline=ACCENT, tags="ui")
            c.create_text(box[0] + 14, (box[1] + box[3]) // 2, text=label, fill=TEXT, font=v.f_body,
                          anchor="w", tags="ui")
            y += row


class ConsolePrompts:
    """messagebox-compatible questions answered inside the console view (modal, like messagebox)."""

    def __init__(self, view):
        self.view = view
        self._vars = []

    def _ask(self, title, message, options, cancel):
        var = tk.StringVar(master=self.view, value="")
        self._vars.append(var)
        self.view.show_overlay(title, message, options, lambda k: var.set(k or cancel), cancel)
        self.view.wait_variable(var)
        self._vars.remove(var)
        return var.get() or cancel

    def cancel_all(self):
        for var in list(self._vars):
            try:
                var.set("__closed__")
            except tk.TclError:
                pass

    def askokcancel(self, title=None, message=None, **_kw):
        return self._ask(title or "", message or "", [("ok", "OK"), ("cancel", "Cancel")], "cancel") == "ok"

    def askyesno(self, title=None, message=None, **_kw):
        return self._ask(title or "", message or "", [("yes", "Yes"), ("no", "No")], "no") == "yes"

    def askyesnocancel(self, title=None, message=None, **_kw):
        ans = self._ask(title or "", message or "",
                        [("yes", "Yes"), ("no", "No"), ("cancel", "Cancel")], "cancel")
        return {"yes": True, "no": False}.get(ans)

    def showinfo(self, title=None, message=None, **_kw):
        self._ask(title or "", message or "", [("ok", "OK")], "ok")
        return "ok"

    showwarning = showinfo
    showerror = showinfo
