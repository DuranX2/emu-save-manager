"""
ui_wiisaves.py - the Wii Save Manager: a console-style, controller-friendly window for the game
saves in Dolphin's (or PrimeHack's) emulated Wii system memory, with the app's backups playing the
part of the SD card.

Each save shows its own animated icon and, when selected, its banner, title and subtitle, with its
size in Wii blocks. Backing up, restoring, moving and deleting go through wiinand.py (whole save
folders, staged and verified; nothing erased outright). Built on ui_console.ConsoleBase.
"""
from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path

import wiinand
from ui_console import ConsoleBase, Image

TILE_PX, BIG_PX = 72, 96              # Wii icons are 48x48


def _png(im):
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def timeline(frames, bounce):
    seq = [i for i, (_img, ticks) in enumerate(frames) for _ in range(ticks)]
    if bounce and len(frames) > 2:
        seq += [i for i, (_img, ticks) in reversed(list(enumerate(frames))[1:-1]) for _ in range(ticks)]
    return seq or [0]


class WiiItem:
    def __init__(self, s: wiinand.WiiSave, game=""):
        self.save = s
        self.name = s.key
        self.title = s.title or game or f"Wii title {s.game_code}"
        self.detail = s.subtitle
        self.frames = s.frames
        self.ticks = timeline(s.frames, s.bounce)

    def frame_at(self, tick):
        return self.ticks[tick % len(self.ticks)]


class WiiConsoleView(ConsoleBase):
    HEADING = "Save Data"
    SUBHEADING = "Wii"
    TILE_ICON = TILE_PX + 24

    def __init__(self, master, dialog):
        super().__init__(master)
        self.dialog = dialog
        self.locs = []                     # [(label, kind, path)]
        self.slot = [None, None]
        self.warned = False

    # ---- data ------------------------------------------------------------------
    def reload(self):
        self.locs = self.dialog.locations()
        if self.slot == [None, None]:
            nands = [l for l in self.locs if l[1] == "nand"]
            backups = next((l for l in self.locs if l[1] == "backup"), None)
            self.slot = [nands[0] if nands else None, backups]
        for i, loc in enumerate(self.slot):
            self.items[i], self.error[i] = [], ""
            if loc is None:
                continue
            _label, kind, path = loc
            try:
                saves = wiinand.list_nand(path) if kind == "nand" else wiinand.list_backups(path)
            except OSError as ex:
                self.error[i] = f"Can't read {path}: {ex}"
                continue
            self.items[i] = sorted((WiiItem(s, self._game(s)) for s in saves),
                                   key=lambda it: (it.title.lower(), it.name))
        self.clamp()
        self.photos = {}
        self.redraw(full=True)

    def _game(self, s):
        titles = getattr(self.dialog.app, "titles", None)
        try:
            return titles.lookup("wii", s.game_code) if titles is not None else ""
        except Exception:  # noqa
            return ""

    def _photo(self, icon, w, h, key):
        if key in self.photos:
            return self.photos[key]
        ph = None
        if icon is not None:
            if Image is not None:
                im = Image.frombytes("RGBA", (icon.width, icon.height), icon.data)
                im = im.resize((w, h), Image.NEAREST if w % icon.width == 0 else Image.BICUBIC)
                ph = tk.PhotoImage(master=self.canvas, data=_png(im))
            else:
                import saveinfo
                ph = tk.PhotoImage(master=self.canvas, data=saveinfo.png_bytes(icon.data, icon.width, icon.height))
        self.photos[key] = ph
        return ph

    def frame_photo(self, it, idx, px):
        if not it.frames:
            return None
        icon = it.frames[min(idx, len(it.frames) - 1)][0]
        return self._photo(icon, px, px, (it.name, id(it), idx, px))

    # ---- hooks -----------------------------------------------------------------
    def tab_label(self, side):
        loc = self.slot[side]
        return f"Slot {side + 1} · {loc[0] if loc else 'empty'}"

    def slot_present(self, side):
        return self.slot[side] is not None

    def empty_text(self, side):
        loc = self.slot[side]
        if loc is not None and loc[1] == "backup":
            return "No backups yet.\nBack up saves from the system memory to keep copies here."
        return "No game saves in the system memory."

    def space_text(self, side):
        items = self.items[side]
        if self.slot[side] is None:
            return ""
        blocks = sum(it.save.blocks for it in items)
        return f"{len(items)} save{'s' if len(items) != 1 else ''} · {blocks} block{'s' if blocks != 1 else ''}"

    def mark_key(self, it):
        return it.name

    def tile_title(self, it):
        return it.title

    def tile_image(self, it):
        return self.frame_photo(it, it.frame_at(self.frame), TILE_PX)

    def marked_text(self, n):
        blocks = sum(it.save.blocks for it in self.items[self.side] if it.name in self.marks[self.side])
        return f"{n} marked · {blocks} blocks"

    def on_view(self):
        self.main_menu()

    def draw_info(self, it, box):
        c = self.canvas
        px0, py0, px1, py1 = box
        panel = self.panel_image(("wii", it.name, id(it)), self._corners(it), px1 - px0, py1 - py0)
        if panel:
            c.create_image(px0, py0, image=panel, anchor="nw", tags="ui")
        y = py0 + 18
        s = it.save
        if s.banner is not None:
            bw = 384 if (px1 - px0) >= 384 + 32 else 288
            ban = self._photo(s.banner, bw, bw // 3, (it.name, id(it), "banner", bw))
            if ban:
                c.create_image((px0 + px1) // 2, y, image=ban, anchor="n", tags="ui")
                y += bw // 3 + 16
        c.create_image(px0 + 16, y, anchor="nw", tags=("ui", "big"))
        self._update_big()
        tx, tw = px0 + 16 + BIG_PX + 14, px1 - (px0 + 16 + BIG_PX + 14) - 16
        c.create_text(tx, y + 2, text=it.title, fill="#e9edff", font=self.f_title, anchor="nw", width=tw, tags="ui")
        ty = y + 6 + self.f_title.metrics("linespace") * (2 if self.f_title.measure(it.title) > tw else 1)
        if it.detail:
            c.create_text(tx, ty, text=it.detail, fill="#e9edff", font=self.f_body, anchor="nw", width=tw, tags="ui")
        y += BIG_PX + 14
        self.text_rows(px0 + 16, y, [
            f"{s.blocks} block{'s' if s.blocks != 1 else ''}  ·  {s.game_code}  ·  {s.type_name}",
            f"{s.modified:%Y-%m-%d  %H:%M}" if s.modified else "",
            f"{s.n_files} file{'s' if s.n_files != 1 else ''}",
            "A real Wii won't copy this save to an SD card" if s.no_copy else ""], px1 - px0 - 32)

    def _corners(self, it):
        src = it.save.banner or (it.frames[0][0] if it.frames else None)
        if src is None or Image is None:
            return [(40, 50, 80), (30, 40, 70), (20, 25, 50), (10, 12, 30)]
        im = Image.frombytes("RGBA", (src.width, src.height), src.data)
        bg = Image.new("RGBA", im.size, (30, 30, 60, 255))
        bg.alpha_composite(im)
        return [tuple(int(v * 0.55) for v in px) for px in bg.convert("RGB").resize((2, 2), Image.BOX).getdata()]

    def _update_big(self):
        it = self.current()
        if it is None or not self.canvas.find_withtag("big"):
            return
        ph = self.frame_photo(it, it.frame_at(self.frame), BIG_PX) or self.placeholder(BIG_PX)
        if ph:
            self.canvas.itemconfigure("big", image=ph)

    def on_tick(self):
        for i, it in enumerate(self.items[self.side]):
            if len(it.ticks) > 1:
                ph = self.tile_image(it)
                if ph is not None:
                    self.canvas.itemconfigure(f"tile{i}", image=ph)
        self._update_big()
        return False

    # ---- actions -----------------------------------------------------------------
    def _close_dolphin(self):
        if self.warned:
            return True
        ok = self.prompts.askokcancel(
            "Close Dolphin first", "Dolphin writes Wii saves while a game runs, so changes made now "
            "could be undone. Make sure Dolphin (and PrimeHack) are closed, then choose OK.")
        self.warned = ok
        return ok

    def _dest_for(self, loc):
        _label, kind, path = loc
        return (lambda s: wiinand.nand_path(path, s)) if kind == "nand" else (lambda s: wiinand.backup_path(path, s))

    def _conflict_for(self, loc):
        def ask(s, old):
            when = f"\n\nThe one there is from {old.modified:%Y-%m-%d %H:%M}" if old.modified else ""
            when += f"; this one from {s.modified:%Y-%m-%d %H:%M}." if s.modified and old.modified else ""
            return self.prompts.choose(
                "Already there", f"“{s.title or s.game_code}” already has a save in {loc[0]}.{when}",
                [("replace", "Replace it (the old one is kept as a safety copy)"), ("skip", "Skip it"),
                 ("cancel", "Cancel")], "cancel")
        return ask

    def _transfer(self, move):
        saves = [it.save for it in self.selection()]
        dest = self.slot[1 - self.side]
        if not saves or dest is None or not self._close_dolphin():
            return
        verb = {("nand", "backup"): "Backed up", ("backup", "nand"): "Restored"}.get(
            (self.slot[self.side][1], dest[1]), "Copied")
        if move:
            verb = "Moved"
        try:
            rep = wiinand.transfer(saves, self._dest_for(dest), move, self.dialog.backup_dir,
                                   conflict=self._conflict_for(dest), info=dest[1] == "backup")
        except (OSError, wiinand.WiiError) as ex:
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
                                  "\n".join(f"• {s.title or s.game_code}: {why}" for s, why in rep.refused))
        n = len(rep.done)
        if n:
            self.toast(f"{verb} {n} save{'s' if n != 1 else ''}" + (f" to {where}" if where and verb != "Deleted" else ""))
        elif rep.skipped and not rep.refused:
            self.toast("Nothing to do: the saves are already there")

    def _delete(self):
        saves = [it.save for it in self.selection()]
        if not saves or not self._close_dolphin():
            return
        what = f"{len(saves)} saves" if len(saves) > 1 else f"the save for “{saves[0].title or saves[0].game_code}”"
        if not self.prompts.askyesno("Delete", f"Delete {what} from {self.slot[self.side][0]}?\n\n"
                                     "A safety copy is kept in the backups' _wii_safety folder."):
            return
        try:
            rep = wiinand.delete(saves, self.dialog.backup_dir)
        except OSError as ex:
            self.prompts.showerror("Couldn't delete", str(ex))
            rep = None
        self._report(rep, "Deleted")

    def save_menu(self):
        sel = self.selection()
        if not sel:
            return
        here, there = self.slot[self.side], self.slot[1 - self.side]
        what = f"{len(sel)} saves" if len(sel) > 1 else sel[0].title
        opts = []
        if there is not None and there != here:
            if here[1] == "nand" and there[1] == "backup":
                opts += [("copy", f"Back up to {there[0]}"), ("move", f"Move to {there[0]}")]
            elif here[1] == "backup" and there[1] == "nand":
                opts += [("copy", f"Restore to {there[0]}"), ("move", f"Move to {there[0]}")]
            else:
                opts += [("copy", f"Copy to {there[0]}"), ("move", f"Move to {there[0]}")]
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
        lines = [f"Game: {s.game_code} ({s.type_name})", f"Title ID: {s.hi.upper()}-{s.lo.upper()}",
                 f"Size: {s.blocks} block{'s' if s.blocks != 1 else ''} ({s.size:,} bytes in {s.n_files} files)",
                 f"Folder: {s.path}"]
        if s.modified:
            lines.append(f"Last changed: {s.modified:%Y-%m-%d %H:%M}")
        lines.append(f"Icon: {len(s.frames)} frame{'s' if len(s.frames) != 1 else ''}"
                     + (", plays back and forth" if s.bounce and len(s.frames) > 2 else ""))
        if s.no_copy:
            lines.append("The game marked it \"no copy\": a real Wii won't copy it to an SD card. "
                         "Backups here are plain copies of Dolphin's folder.")
        self.show_overlay(it.title + (f" — {it.detail}" if it.detail else ""), "\n".join(lines),
                          [("ok", "Close")], lambda k: None)

    def card_menu(self, side):
        cur = self.slot[side]
        opts = []
        for k, loc in enumerate(self.locs):
            label, kind, path = loc
            n = len(wiinand.list_nand(path) if kind == "nand" else wiinand.list_backups(path))
            mark = "● " if cur == loc else ""
            opts.append((str(k), f"{mark}{label}  ·  {n} save{'s' if n != 1 else ''}"))
        opts.append(("cancel", "Cancel"))
        if not self.locs:
            self.show_overlay(f"Slot {side + 1}", "Dolphin's Wii folder wasn't found. Set it with "
                              "'Locations…' in the main window.", [("ok", "OK")], lambda k: None)
            return

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
                    folder = Path(loc[2]) / ("title" if loc[1] == "nand" else "")
                    folder.mkdir(parents=True, exist_ok=True)
                    opener = getattr(self.dialog.app, "open_folder", None)
                    if opener:
                        opener(folder)
            elif key == "close":
                self.dialog.destroy()
        self.show_overlay("Wii Save Manager", "", opts, chosen)


class WiiSaveDialog(tk.Toplevel):
    def __init__(self, app, emu="Dolphin"):
        super().__init__(app)
        self.app = app
        self.emu = emu if emu in ("Dolphin", "PrimeHack") else "Dolphin"
        self.title("Wii Save Manager")
        self.backup_dir = Path(app.backup_dir)
        self.saves_dir = self.backup_dir / "Wii saves"
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh - 40}+0+0" if sh <= 820 else "1200x740")
        self.minsize(820, 520)
        self.view = WiiConsoleView(self, self)
        self.view.pack(fill="both", expand=True)
        self.msg = self.view.prompts
        self.after(10, self.view.start)

    def locations(self):
        out = []
        order = [self.emu] + [e for e in ("Dolphin", "PrimeHack") if e != self.emu]
        for emu in order:
            try:
                path = self.app.resolve(emu).get("Wii Saves")
            except Exception:  # noqa
                path = None
            if path and Path(path).is_dir():
                out.append((f"{emu} system memory", "nand", Path(path)))
        out.append(("Backups", "backup", self.saves_dir))
        return out
