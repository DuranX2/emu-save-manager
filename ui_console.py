"""
ui_console.py - the shared parts of the console-style card managers (PS2, GameCube, …).

ConsoleBase is a full-window, controller-friendly canvas: two slots shown as tabs, a grid of
saves, an information panel for the selected save, button hints, toasts, and menus and
questions drawn inside the window (Overlay / ConsolePrompts, which answer messagebox-style
questions so the existing safety checks can be driven with a controller). Each system's view
subclasses it and supplies its saves, tiles, info panel and menus.

The look is an original design in the spirit of a console's memory card screen: no console
artwork, fonts or sounds are used.
"""
from __future__ import annotations

import io
import math
import random
import time
import tkinter as tk
import tkinter.font as tkfont

import gamepad

try:
    from PIL import Image, ImageDraw, ImageFilter
except Exception:  # noqa
    Image = None

TICK_MS = 66
TILE_W, TILE_H = 132, 150
BG_TOP, BG_BOTTOM = (10, 14, 40), (4, 5, 16)
TEXT, DIM, ACCENT, MARK = "#e9edff", "#9aa5d4", "#7fd4ff", "#ffd479"


def png_bytes(img) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


class ConsoleBase(tk.Frame):
    """Subclasses set HEADING / SUBHEADING and implement the hooks marked below."""

    HEADING = "Memory Card"
    SUBHEADING = ""
    TILE_ICON = 88

    def __init__(self, master):
        super().__init__(master, bg="#05060f", highlightthickness=0)
        self.canvas = tk.Canvas(self, bg="#05060f", highlightthickness=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)
        self.side = 0
        self.items = [[], []]
        self.cursor = [0, 0]
        self.scroll = [0, 0]
        self.marks = [set(), set()]
        self.error = ["", ""]
        self.photos = {}
        self.static = {}
        self.overlay = None
        self.toast_text, self.toast_until = "", 0.0
        self.frame = 0
        self.nav = None
        self.running = False
        self.prompts = ConsolePrompts(self)
        self._layout_size = (0, 0)
        self._bokeh = [[random.random(), random.random(), random.uniform(0.6, 1.4)] for _ in range(12)]
        self._fonts()
        self.canvas.bind("<Configure>", lambda e: self.redraw(full=True))
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-Button-1>", self._double)
        self.canvas.bind("<MouseWheel>", lambda e: self.act("up" if e.delta > 0 else "down"))
        self.canvas.bind("<Button-4>", lambda e: self.act("up"))
        self.canvas.bind("<Button-5>", lambda e: self.act("down"))
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ---- hooks (override) ----------------------------------------------------
    def reload(self):
        """Read both slots into self.items (and call self.after_reload())."""

    def tab_label(self, side) -> str:
        return f"Slot {side + 1}"

    def space_text(self, side) -> str:
        return ""

    def slot_present(self, side) -> bool:
        return True

    def empty_text(self, side) -> str:
        return "This card has no saves."

    def mark_key(self, it):
        return it.name

    def tile_title(self, it) -> str:
        return it.name

    def tile_image(self, it):
        return None

    def draw_info(self, it, box):
        """Draw the selected save's panel inside box (x0, y0, x1, y1)."""

    def on_cursor(self):
        """The selected save changed."""

    def on_tick(self) -> bool:
        """Called every tick; return True to redraw everything."""
        return False

    def save_menu(self):
        pass

    def card_menu(self, side):
        pass

    def main_menu(self):
        pass

    def on_view(self):
        pass

    def footer(self):
        return [("A", "Options"), ("X", "Mark"), ("Y", "Menu"), ("B", "Back"), ("LB/RB", "Slot")]

    EDGE_SWITCH = True            # moving left/right off the grid's edge switches slot

    def tab_items(self):
        """[(label, active)] for the tabs across the top (default: the two slots)."""
        return [(self.tab_label(0), self.side == 0), (self.tab_label(1), self.side == 1)]

    def on_tab(self, i):
        """A tab was clicked."""
        if i == self.side:
            self.card_menu(i)
        else:
            self.switch(i)

    def on_prev_next(self, step):
        """LB / RB (step -1 / +1)."""
        self.switch(0 if step < 0 else 1)

    def on_back(self):
        """B with nothing marked."""
        self.main_menu()

    def on_stop(self):
        pass

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
            self.on_stop()
            self.prompts.cancel_all()

    def current(self):
        items = self.items[self.side]
        return items[self.cursor[self.side]] if items else None

    def clamp(self):
        for i in (0, 1):
            names = {self.mark_key(it) for it in self.items[i]}
            self.marks[i] &= names
            self.cursor[i] = min(self.cursor[i], max(0, len(self.items[i]) - 1))

    # ---- actions -------------------------------------------------------------
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
                    if self.EDGE_SWITCH:
                        self.switch(0)
                    return
                c -= 1
            elif action == "right":
                if c % cols == cols - 1 or c == len(items) - 1:
                    if self.EDGE_SWITCH:
                        self.switch(1)
                    return
                c += 1
            elif action == "up":
                if c - cols >= 0:
                    c -= cols
            elif action == "down":
                if c + cols < len(items):
                    c += cols
                elif c // cols < (len(items) - 1) // cols:      # a shorter last row: go to its end
                    c = len(items) - 1
            self.move(c)
        elif action in ("left", "right"):
            if self.EDGE_SWITCH:
                self.switch(0 if action == "left" else 1)
        elif action in ("prev", "next"):
            self.on_prev_next(-1 if action == "prev" else 1)
        elif action == "mark" and items:
            self.marks[self.side] ^= {self.mark_key(items[c])}
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
                self.on_back()
        elif action == "view":
            self.on_view()

    def move(self, c):
        if c != self.cursor[self.side]:
            self.cursor[self.side] = c
            self.on_cursor()
            self.redraw()

    def switch(self, side):
        if side != self.side:
            self.side = side
            self.on_cursor()
            self.redraw()

    def selection(self) -> list:
        """Marked saves on the active side (in order), or the one under the cursor."""
        items = self.items[self.side]
        marked = [it for it in items if self.mark_key(it) in self.marks[self.side]]
        if marked:
            return marked
        it = self.current()
        return [it] if it else []

    def show_overlay(self, title, message, options, on_choose, cancel_key="cancel"):
        self.overlay = Overlay(self, title, message, options, on_choose, cancel_key)
        self.redraw()

    def close_overlay(self):
        self.overlay = None
        self.redraw()

    def toast(self, text, seconds=4.0):
        self.toast_text, self.toast_until = text, time.monotonic() + seconds
        self.redraw()

    # ---- mouse -----------------------------------------------------------------
    def _click(self, e):
        self.canvas.focus_set()
        if self.overlay is not None:
            self.overlay.click(e.x, e.y)
            return
        for i, (x0, y0, x1, y1) in self._tab_boxes():
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self.on_tab(i)
                return
        idx = self._tile_at(e.x, e.y)
        if idx is not None:
            self.move(idx)

    def _double(self, e):
        if self.overlay is None and self._tile_at(e.x, e.y) is not None:
            self.save_menu()

    # ---- layout ----------------------------------------------------------------
    def geom(self):
        w = max(640, self.canvas.winfo_width())
        h = max(400, self.canvas.winfo_height())
        return w, h, 64, 46, int(w * 0.62)          # width, height, header, footer, grid/panel split

    def _cols(self):
        w, h, head, foot, split = self.geom()
        return max(2, (split - 40) // TILE_W)

    def tile_box(self, idx):
        w, h, head, foot, split = self.geom()
        cols = self._cols()
        tw = (split - 40) // cols
        row, col = divmod(idx, cols)
        x0 = 24 + col * tw
        y0 = head + 16 + (row - self.scroll[self.side]) * TILE_H
        return x0, y0, x0 + tw, y0 + TILE_H

    def _tile_at(self, x, y):
        for i in range(len(self.items[self.side])):
            x0, y0, x1, y1 = self.tile_box(i)
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i
        return None

    def _tab_boxes(self):
        """[(tab index, box)] for the tabs that fit; the active tab is always shown."""
        w, h, head, foot, split = self.geom()
        x_start = 50 + self.f_head.measure(self.HEADING) + self.f_small.measure(self.SUBHEADING) + 30
        x_end = w - 24 - self.f_small.measure(self.space_text(self.side)) - 30
        tabs = self.tab_items()
        widths = [self.f_tab.measure(label) + 30 for label, _on in tabs]
        active = next((i for i, (_l, on) in enumerate(tabs) if on), 0)
        lo = hi = active
        room = x_end - x_start - 40                          # leave room for the ‹ › hints
        used = widths[active] if tabs else 0
        while True:                                          # grow around the active tab
            grew = False
            if hi + 1 < len(tabs) and used + widths[hi + 1] + 12 <= room:
                hi += 1
                used += widths[hi] + 12
                grew = True
            if lo - 1 >= 0 and used + widths[lo - 1] + 12 <= room:
                lo -= 1
                used += widths[lo] + 12
                grew = True
            if not grew:
                break
        self._tab_more = (lo > 0, hi < len(tabs) - 1)
        x = x_start + (20 if lo > 0 else 0)
        boxes = []
        for i in range(lo, hi + 1) if tabs else ():
            boxes.append((i, (x, 14, x + widths[i], head - 10)))
            x += widths[i] + 12
        return boxes

    def _keep_visible(self):
        w, h, head, foot, split = self.geom()
        rows_vis = max(1, (h - head - foot - 24) // TILE_H)
        row = self.cursor[self.side] // self._cols()
        s = self.scroll[self.side]
        if row < s:
            s = row
        elif row >= s + rows_vis:
            s = row - rows_vis + 1
        self.scroll[self.side] = max(0, s)

    # ---- pictures --------------------------------------------------------------
    def photo(self, key, png=None, img=None):
        """A cached PhotoImage for static pictures (backgrounds, panels, glyphs)."""
        ph = self.static.get(key)
        if ph is None:
            if img is not None:
                png = png_bytes(img)
            ph = tk.PhotoImage(master=self.canvas, data=png) if png else None
            self.static[key] = ph
        return ph

    def drop_static(self, kind):
        self.static = {k: v for k, v in self.static.items() if k[0] != kind}

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
            self.drop_static("bg")
            self.photo(key, img=img)
        return self.static.get(key)

    def _bokeh_img(self, r):
        key = ("bokeh", r)
        if key not in self.static and Image is not None:
            size = r * 4
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((r, r, 3 * r, 3 * r), fill=(150, 190, 255, 38))
            im = im.filter(ImageFilter.GaussianBlur(r * 0.6))
            self.photo(key, img=im)
        return self.static.get(key)

    def panel_image(self, key, corners, w, h):
        """A rounded panel filled with a four-corner gradient (TL, TR, BL, BR colours)."""
        k = ("panel", key, w, h)
        if k not in self.static and Image is not None:
            small = Image.new("RGB", (2, 2))
            small.putdata(list(corners))
            grad = small.resize((w, h), Image.BILINEAR).convert("RGBA")
            grad.alpha_composite(Image.new("RGBA", (w, h), (0, 0, 0, 90)))
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=18, fill=235)
            grad.putalpha(mask)
            self.drop_static("panel")
            self.photo(k, img=grad)
        return self.static.get(k)

    def placeholder(self, size):
        key = ("placeholder", size)
        if key not in self.static and Image is not None:
            im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            m = size // 5
            d.rounded_rectangle((m, m // 2, size - m, size - m // 2), radius=size // 10,
                                fill=(70, 80, 120, 255), outline=(150, 165, 220, 255), width=2)
            d.rectangle((size // 2 - m, m, size // 2 + m, m + m), fill=(40, 46, 70, 255))
            self.photo(key, img=im)
        return self.static.get(key)

    def glyph(self, x, y, letters, label):
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

    def text_rows(self, x, y, rows, width, font=None, fill="#d5dbf7"):
        """Draw lines of text downwards; returns the next y."""
        font = font or self.f_small
        for r in rows:
            if r:
                self.canvas.create_text(x, y, text=r, fill=fill, font=font, anchor="nw", width=width, tags="ui")
                y += font.metrics("linespace") + 2
        return y

    # ---- drawing -----------------------------------------------------------------
    def redraw(self, full=False):
        if not self.running and not full:
            return
        c = self.canvas
        w, h, head, foot, split = self.geom()
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
        c.create_text(24, head // 2, text=self.HEADING, fill=TEXT, font=self.f_head, anchor="w", tags="ui")
        if self.SUBHEADING:
            c.create_text(24 + self.f_head.measure(self.HEADING) + 8, head // 2 + 4, text=self.SUBHEADING,
                          fill=DIM, font=self.f_small, anchor="w", tags="ui")
        tabs = self.tab_items()
        boxes = self._tab_boxes()
        for i, (x0, y0, x1, y1) in boxes:
            label, on = tabs[i]
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2 - 2, text=label,
                          fill=TEXT if on else DIM, font=self.f_tab, tags="ui")
            if on:
                c.create_line(x0 + 8, y1, x1 - 8, y1, fill=ACCENT, width=3, tags="ui")
        if boxes and self._tab_more[0]:
            c.create_text(boxes[0][1][0] - 14, head // 2 - 2, text="‹", fill=DIM, font=self.f_tab, tags="ui")
        if boxes and self._tab_more[1]:
            c.create_text(boxes[-1][1][2] + 12, head // 2 - 2, text="›", fill=DIM, font=self.f_tab, tags="ui")
        c.create_text(w - 24, head // 2, text=self.space_text(self.side), fill=DIM, font=self.f_small,
                      anchor="e", tags="ui")
        c.create_line(24, head, w - 24, head, fill="#2a3360", tags="ui")
        # grid
        items = self.items[self.side]
        if self.error[self.side]:
            c.create_text(split // 2, h // 2, text=self.error[self.side], fill=MARK, width=split - 80,
                          font=self.f_body, tags="ui")
        elif not self.slot_present(self.side):
            c.create_text(split // 2, h // 2, text="Nothing in this slot.\nPress Ⓐ or Enter to choose.",
                          fill=DIM, justify="center", font=self.f_body, tags="ui")
        elif not items:
            c.create_text(split // 2, h // 2, text=self.empty_text(self.side), fill=DIM, justify="center",
                          width=split - 80, font=self.f_body, tags="ui")
        for i, it in enumerate(items):
            x0, y0, x1, y1 = self.tile_box(i)
            if y0 < head or y1 > h - foot + 6:          # only whole tiles: never over the header or footer
                continue
            cx = (x0 + x1) // 2
            sel = i == self.cursor[self.side]
            if sel:
                c.create_rectangle(x0 + 6, y0 + 2, x1 - 6, y1 - 4, outline=ACCENT, width=2, tags="ui")
            ph = self.tile_image(it) or self.placeholder(self.TILE_ICON - 16)
            if ph:
                c.create_image(cx, y0 + 12 + self.TILE_ICON // 2, image=ph, tags=("ui", f"tile{i}"))
            full_t = self.tile_title(it)
            title = full_t
            while self.f_small.measure(title) > (x1 - x0 - 14) and len(title) > 3:
                title = title[:-2]
            if title != full_t:
                title = title.rstrip() + "…"
            c.create_text(cx, y0 + self.TILE_ICON + 24, text=title, fill=TEXT if sel else DIM,
                          font=self.f_small, tags="ui")
            if self.mark_key(it) in self.marks[self.side]:
                c.create_oval(x1 - 34, y0 + 8, x1 - 14, y0 + 28, fill=MARK, outline="", tags="ui")
                c.create_text(x1 - 24, y0 + 18, text="✓", fill="#1a1a1a", font=self.f_glyph, tags="ui")
        # info panel
        it = self.current()
        box = (split + 8, head + 16, w - 24, h - foot - 12)
        if it is not None and box[2] - box[0] > 120:
            self.draw_info(it, box)
            n = len(self.marks[self.side])
            if n:
                c.create_text(box[0] + 16, box[3] - 16, text=self.marked_text(n), fill=MARK,
                              font=self.f_body, anchor="sw", tags="ui")
        # footer
        c.create_line(24, h - foot, w - 24, h - foot, fill="#2a3360", tags="ui")
        x, y = 36, h - foot // 2
        for letter, label in self.footer():
            x = self.glyph(x, y, letter, label)
        c.create_text(w - 24, y, text="Keys: arrows · Enter · X · Tab · Esc · Q/E", fill=DIM,
                      font=self.f_small, anchor="e", tags="ui")
        if self.toast_text and time.monotonic() < self.toast_until:
            tw = self.f_body.measure(self.toast_text) + 40
            c.create_rectangle(w // 2 - tw // 2, h - foot - 54, w // 2 + tw // 2, h - foot - 18,
                               fill="#1b2450", outline=ACCENT, tags="ui")
            c.create_text(w // 2, h - foot - 36, text=self.toast_text, fill=TEXT, font=self.f_body, tags="ui")
        if self.overlay is not None:
            self.overlay.draw()

    def marked_text(self, n):
        return f"{n} marked"

    def _draw_bokeh(self, create=False):
        c = self.canvas
        w, h, *_ = self.geom()
        if create:
            c.delete("bokeh")
            for k, b in enumerate(self._bokeh):
                img = self._bokeh_img(int(18 * b[2]))
                if img:
                    c.create_image(b[0] * w, b[1] * h, image=img, tags=("bokeh", f"bokeh{k}"))
            if c.find_withtag("bg"):
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
        self.frame += 1
        try:
            redraw = self.on_tick()
        except Exception:  # noqa - an animation problem must not stop the view
            redraw = False
        if redraw and self.overlay is None:
            self.redraw()
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
        self.top = 0                  # first option shown when the list is longer than the window
        self.boxes = []               # [(option index, box)]

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
        for i, (x0, y0, x1, y1) in self.boxes:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.index = i
                self.choose(self.options[i][0])
                return

    def draw(self):
        v, c = self.view, self.view.canvas
        w, h, *_ = v.geom()
        bw = min(580, w - 80)
        msg_h = 0
        if self.message:
            tmp = c.create_text(0, 0, text=self.message, font=v.f_body, width=bw - 48, anchor="nw")
            bb = c.bbox(tmp)
            c.delete(tmp)
            msg_h = (bb[3] - bb[1]) + 16 if bb else 0
        row = v.f_body.metrics("linespace") + 14
        bh = min(70 + msg_h + row * len(self.options) + 16, h - 40)
        fit = max(1, (bh - 70 - msg_h - 16) // row)
        if len(self.options) > fit:
            fit = max(1, fit - 1)                         # room for the "more" hint
        if self.index < self.top:
            self.top = self.index
        elif self.index >= self.top + fit:
            self.top = self.index - fit + 1
        self.top = max(0, min(self.top, max(0, len(self.options) - fit)))
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
        shown = list(enumerate(self.options))[self.top:self.top + fit]
        for i, (_k, label) in shown:
            box = (x0 + 16, y, x0 + bw - 16, y + row - 4)
            self.boxes.append((i, box))
            if i == self.index:
                c.create_rectangle(*box, fill="#24336e", outline=ACCENT, tags="ui")
            room = bw - 64
            if v.f_body.measure(label) > room:
                while len(label) > 4 and v.f_body.measure(label + "…") > room:
                    label = label[:-1]
                label = label.rstrip() + "…"
            c.create_text(box[0] + 14, (box[1] + box[3]) // 2, text=label, fill=TEXT, font=v.f_body,
                          anchor="w", tags="ui")
            y += row
        if len(shown) < len(self.options):
            more = (("▲ " if self.top > 0 else "") + f"{self.index + 1} of {len(self.options)}"
                    + (" ▼" if self.top + fit < len(self.options) else ""))
            c.create_text(x0 + bw // 2, y + row // 2 - 6, text=more, fill=DIM, font=v.f_small, tags="ui")


class ConsolePrompts:
    """messagebox-compatible questions answered inside a console view (modal, like messagebox)."""

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

    def choose(self, title, message, options, cancel="cancel"):
        """Ask with any options [(key, label)]; returns the chosen key."""
        return self._ask(title, message, options, cancel)

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
