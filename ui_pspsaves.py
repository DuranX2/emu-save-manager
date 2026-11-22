"""
ui_pspsaves.py - the PSP Save Manager: a console-style, controller-friendly window for the saves
on PPSSPP's memory stick, with the app's backups as a second place to keep them.

Each save shows its own icon (ICON0.PNG); the selected save's background picture (PIC1.PNG) fills
the information panel behind its title, save title and detail lines. Backing up, restoring,
moving and deleting go through pspsaves.py (whole folders, staged and verified; nothing erased
outright). Built on ui_console.ConsoleBase.
"""
from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path

import pspsaves
from ui_console import ConsoleBase, Image

TILE_W, TILE_H = 112, 62              # ICON0 is 144x80
BIG_W, BIG_H = 288, 160

try:
    from PIL import ImageDraw, ImageEnhance
except Exception:  # noqa
    ImageDraw = ImageEnhance = None


def _png(im):
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _size(n):
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


class PSPItem:
    def __init__(self, s: pspsaves.PSPSave):
        self.save = s
        self.name = s.key
        self.title = s.label
        self.detail = s.save_title


class PSPConsoleView(ConsoleBase):
    HEADING = "Save Data"
    SUBHEADING = "PSP"
    TILE_ICON = 88

    def __init__(self, master, dialog):
        super().__init__(master)
        self.dialog = dialog
        self.locs = []
        self.slot = [None, None]
        self.warned = False

    # ---- data ------------------------------------------------------------------
    def reload(self):
        self.locs = self.dialog.locations()
        if self.slot == [None, None]:
            stick = next((l for l in self.locs if l[1] == "stick"), None)
            backups = next((l for l in self.locs if l[1] == "backup"), None)
            self.slot = [stick, backups]
        for i, loc in enumerate(self.slot):
            self.items[i], self.error[i] = [], ""
            if loc is None:
                continue
            try:
                self.items[i] = [PSPItem(s) for s in pspsaves.list_saves(loc[2], loc[1])]
            except OSError as ex:
                self.error[i] = f"Can't read {loc[2]}: {ex}"
        self.clamp()
        self.photos = {}
        self.redraw(full=True)

    def icon_photo(self, it, w, h):
        key = (it.name, id(it), "icon", w)
        if key not in self.photos:
            ph = None
            if it.save.icon:
                try:
                    if Image is not None:
                        im = Image.open(io.BytesIO(it.save.icon)).convert("RGBA")
                        k = min(w / im.width, h / im.height)          # fit, larger or smaller
                        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
                        ph = tk.PhotoImage(master=self.canvas, data=_png(im))
                    else:
                        ph = tk.PhotoImage(master=self.canvas, data=it.save.icon)
                except Exception:  # noqa - a broken picture just isn't shown
                    ph = None
            self.photos[key] = ph
        return self.photos[key]

    def background_panel(self, it, w, h):
        """The save's PIC1 filling the panel (dimmed, rounded), or a gradient from its icon."""
        key = ("psp-panel", it.name, id(it), w, h)
        if key in self.static:
            return self.static[key]
        if Image is None or not it.save.background:
            return self.panel_image(("psp", it.name, id(it)), self._corners(it), w, h)
        try:
            im = Image.open(io.BytesIO(it.save.background)).convert("RGB")
            scale = max(w / im.width, h / im.height)
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
            x, y = (im.width - w) // 2, (im.height - h) // 2
            im = im.crop((x, y, x + w, y + h))
            im = ImageEnhance.Brightness(im).enhance(0.45).convert("RGBA")
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=18, fill=245)
            im.putalpha(mask)
        except Exception:  # noqa
            return self.panel_image(("psp", it.name, id(it)), self._corners(it), w, h)
        self.drop_static("psp-panel")
        return self.photo(key, img=im)

    def _corners(self, it):
        if not it.save.icon or Image is None:
            return [(40, 40, 70), (30, 30, 60), (20, 20, 45), (10, 10, 30)]
        try:
            im = Image.open(io.BytesIO(it.save.icon)).convert("RGBA")
            bg = Image.new("RGBA", im.size, (30, 30, 60, 255))
            bg.alpha_composite(im)
            return [tuple(int(v * 0.5) for v in px) for px in bg.convert("RGB").resize((2, 2), Image.BOX).getdata()]
        except Exception:  # noqa
            return [(40, 40, 70), (30, 30, 60), (20, 20, 45), (10, 10, 30)]

    # ---- hooks -----------------------------------------------------------------
    def tab_label(self, side):
        loc = self.slot[side]
        return f"Slot {side + 1} · {loc[0] if loc else 'empty'}"

    def slot_present(self, side):
        return self.slot[side] is not None

    def empty_text(self, side):
        loc = self.slot[side]
        if loc is not None and loc[1] == "backup":
            return "No backups yet.\nBack up saves from the memory stick to keep copies here."
        return "No saves on the memory stick."

    def space_text(self, side):
        if self.slot[side] is None:
            return ""
        items = self.items[side]
        return f"{len(items)} save{'s' if len(items) != 1 else ''} · {_size(sum(it.save.size for it in items))}"

    def mark_key(self, it):
        return it.name

    def tile_title(self, it):
        """The game's title, or the save's own title when the game has several saves here."""
        same = sum(1 for o in self.items[self.side] if o.title == it.title)
        return it.detail or it.title if same > 1 else it.title

    def tile_image(self, it):
        return self.icon_photo(it, TILE_W, TILE_H)

    def marked_text(self, n):
        size = sum(it.save.size for it in self.items[self.side] if it.name in self.marks[self.side])
        return f"{n} marked · {_size(size)}"

    def on_view(self):
        self.main_menu()

    def draw_info(self, it, box):
        c = self.canvas
        px0, py0, px1, py1 = box
        panel = self.background_panel(it, px1 - px0, py1 - py0)
        if panel:
            c.create_image(px0, py0, image=panel, anchor="nw", tags="ui")
        y = py0 + 20
        bw = BIG_W if px1 - px0 >= BIG_W + 32 else (px1 - px0 - 32)
        big = self.icon_photo(it, bw, bw * BIG_H // BIG_W) or self.placeholder(96)
        if big:
            c.create_image((px0 + px1) // 2, y, image=big, anchor="n", tags="ui")
            y += bw * BIG_H // BIG_W + 16
        tw = px1 - px0 - 32
        c.create_text(px0 + 16, y, text=it.title, fill="#ffffff", font=self.f_title, anchor="nw", width=tw, tags="ui")
        y += self.f_title.metrics("linespace") * (2 if self.f_title.measure(it.title) > tw else 1) + 4
        s = it.save
        if s.save_title:
            c.create_text(px0 + 16, y, text=s.save_title, fill="#ffffff", font=self.f_body, anchor="nw",
                          width=tw, tags="ui")
            y += self.f_body.metrics("linespace") + 2
        if s.detail:
            lines = s.detail.splitlines()[:4]
            c.create_text(px0 + 16, y, text="\n".join(lines), fill="#e6e9f8", font=self.f_small, anchor="nw",
                          width=tw, tags="ui")
            y += (self.f_small.metrics("linespace") + 1) * len(lines) + 8
        self.text_rows(px0 + 16, y, [
            f"{_size(s.size)}  ·  {s.game_id}" + (f"  ·  {s.region}" if s.region else ""),
            f"{s.modified:%Y-%m-%d  %H:%M}" if s.modified else "", s.name], tw, fill="#d9def5")

    # ---- actions -----------------------------------------------------------------
    def _close_ppsspp(self):
        if self.warned:
            return True
        ok = self.prompts.askokcancel(
            "Close PPSSPP first", "PPSSPP writes saves while a game runs, so changes made now could be "
            "undone. Make sure PPSSPP is closed, then choose OK.")
        self.warned = ok
        return ok

    def _conflict(self, where):
        def ask(s, old):
            when = ""
            if old.modified and s.modified:
                when = (f"\n\nThe one there is from {old.modified:%Y-%m-%d %H:%M}; "
                        f"this one from {s.modified:%Y-%m-%d %H:%M}.")
            return self.prompts.choose(
                "Already there", f"“{s.label}” ({s.save_title or s.name}) is already in {where}.{when}",
                [("replace", "Replace it (the old one is kept as a safety copy)"), ("skip", "Skip it"),
                 ("cancel", "Cancel")], "cancel")
        return ask

    def _transfer(self, move):
        saves = [it.save for it in self.selection()]
        dest = self.slot[1 - self.side]
        if not saves or dest is None or not self._close_ppsspp():
            return
        verb = "Moved" if move else {("stick", "backup"): "Backed up", ("backup", "stick"): "Restored"}.get(
            (self.slot[self.side][1], dest[1]), "Copied")
        try:
            rep = pspsaves.transfer(saves, dest[2], move, self.dialog.backup_dir, self._conflict(dest[0]))
        except Exception as ex:  # noqa - OSError, CopyError
            self.prompts.showerror("Couldn't finish", str(ex))
            rep = None
        self._report(rep, verb, dest[0])

    def _report(self, rep, verb, where=""):
        self.marks[self.side].clear()
        self.reload()
        if rep is None:
            return
        if rep.refused:
            self.prompts.showinfo("Some saves weren't changed",
                                  "\n".join(f"• {s.label} ({s.name}): {why}" for s, why in rep.refused))
        n = len(rep.done)
        if n:
            self.toast(f"{verb} {n} save{'s' if n != 1 else ''}" + (f" to {where}" if where and verb != "Deleted" else ""))
        elif rep.skipped and not rep.refused:
            self.toast("Nothing to do: the saves are already there")

    def _delete(self):
        saves = [it.save for it in self.selection()]
        if not saves or not self._close_ppsspp():
            return
        what = f"{len(saves)} saves" if len(saves) > 1 else f"“{saves[0].label}” ({saves[0].save_title or saves[0].name})"
        if not self.prompts.askyesno("Delete", f"Delete {what} from {self.slot[self.side][0]}?\n\n"
                                     "A safety copy is kept in the backups' _psp_safety folder."):
            return
        try:
            rep = pspsaves.delete(saves, self.dialog.backup_dir)
        except OSError as ex:
            self.prompts.showerror("Couldn't delete", str(ex))
            rep = None
        self._report(rep, "Deleted")

    def save_menu(self):
        sel = self.selection()
        if not sel:
            return
        here, there = self.slot[self.side], self.slot[1 - self.side]
        what = f"{len(sel)} saves" if len(sel) > 1 else sel[0].title + (f" — {sel[0].detail}" if sel[0].detail else "")
        opts = []
        if there is not None and there != here:
            word = {("stick", "backup"): "Back up to", ("backup", "stick"): "Restore to"}.get((here[1], there[1]), "Copy to")
            opts += [("copy", f"{word} {there[0]}"), ("move", f"Move to {there[0]}")]
        opts += [("delete", "Delete"), ("details", "Details"), ("cancel", "Cancel")]

        def chosen(key):
            {"copy": lambda: self._transfer(False), "move": lambda: self._transfer(True),
             "delete": self._delete, "details": self.details}.get(key, lambda: None)()
        self.show_overlay(what, "", opts, chosen)

    def details(self):
        it = self.current()
        if it is None:
            return
        s = it.save
        lines = [f"Game: {s.game_id}" + (f" ({s.region})" if s.region else ""), f"Folder: {s.path}",
                 f"Size: {_size(s.size)} in {s.n_files} files"]
        if s.modified:
            lines.append(f"Last changed: {s.modified:%Y-%m-%d %H:%M}")
        if s.detail:
            lines += ["", s.detail]
        self.show_overlay(it.title + (f" — {s.save_title}" if s.save_title else ""), "\n".join(lines),
                          [("ok", "Close")], lambda k: None)

    def card_menu(self, side):
        if not self.locs:
            return
        cur = self.slot[side]
        opts = []
        for k, loc in enumerate(self.locs):
            n = len(pspsaves.list_saves(loc[2], loc[1]))
            opts.append((str(k), f"{'● ' if cur == loc else ''}{loc[0]}  ·  {n} save{'s' if n != 1 else ''}"))
        opts.append(("cancel", "Cancel"))

        def chosen(key):
            if key.isdigit():
                self.slot[side] = self.locs[int(key)]
                self.side = side
                self.cursor[side] = 0
                self.reload()
        self.show_overlay(f"Choose what's in slot {side + 1}", "", opts, chosen)

    def main_menu(self):
        opts = [("slot1", "Choose slot 1…"), ("slot2", "Choose slot 2…"), ("refresh", "Refresh"),
                ("folder", "Open this folder"), ("close", "Close manager"), ("cancel", "Cancel")]

        def chosen(key):
            if key == "slot1":
                self.card_menu(0)
            elif key == "slot2":
                self.card_menu(1)
            elif key == "refresh":
                self.reload()
            elif key == "folder":
                loc = self.slot[self.side]
                if loc is not None:
                    Path(loc[2]).mkdir(parents=True, exist_ok=True)
                    opener = getattr(self.dialog.app, "open_folder", None)
                    if opener:
                        opener(Path(loc[2]))
            elif key == "close":
                self.dialog.destroy()
        self.show_overlay("PSP Save Manager", "", opts, chosen)


class PSPSaveDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("PSP Save Manager")
        self.backup_dir = Path(app.backup_dir)
        self.saves_dir = self.backup_dir / "PSP saves"
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh - 40}+0+0" if sh <= 820 else "1200x740")
        self.minsize(820, 520)
        self.view = PSPConsoleView(self, self)
        self.view.pack(fill="both", expand=True)
        self.msg = self.view.prompts
        self.after(10, self.view.start)

    def locations(self):
        out = []
        try:
            path = self.app.resolve("PPSSPP").get("Saves")
        except Exception:  # noqa
            path = None
        if path:
            out.append(("PPSSPP memory stick", "stick", Path(path)))
        out.append(("Backups", "backup", self.saves_dir))
        return out
