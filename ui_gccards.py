"""
ui_gccards.py - the GameCube Memory Card Manager: a console-style, controller-friendly window
for Dolphin's GCI folder cards (Card A / Card B for each region), plus an "Exported saves" folder
in the backups.

Each save shows its own animated icon (every frame, with the game's timing, looping or going
back and forth) and its banner, with its two comment lines. Copies, moves, deletes and exports go
through gcfolder.py (verified writes; nothing erased outright). Built on ui_console.ConsoleBase.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

import gcfolder
from ui_console import ConsoleBase, Image

TILE_SCALE, BIG_SCALE = 2, 3          # GameCube icons are 32x32
DOLPHIN_EMUS = ("Dolphin", "PrimeHack")


def timeline(frames, bounce) -> list:
    """Frame index for each tick: [0, 1, 1, 2, 2, 2] for speeds 1, 2, 3; bouncing adds the way back."""
    seq = [i for i, (_img, ticks) in enumerate(frames) for _ in range(ticks)]
    if bounce and len(frames) > 2:
        back = [i for i, (_img, ticks) in reversed(list(enumerate(frames))[1:-1]) for _ in range(ticks)]
        seq += back
    return seq or [0]


class GCItem:
    def __init__(self, f: gcfolder.GCFile):
        self.file = f
        s = f.save
        self.name = f.name
        self.title = s.title or s.game_id
        self.detail = s.detail
        self.empty = f.empty
        if f.empty:                        # name the game from its ID; say plainly the data is gone
            self.title = f"Empty save · {s.game_id}"
        self.frames = s.frames
        self.ticks = timeline(s.frames, s.bounce)
        self.banner = s.banner

    def frame_at(self, tick) -> int:
        return self.ticks[tick % len(self.ticks)]


class GCConsoleView(ConsoleBase):
    HEADING = "Memory Card"
    SUBHEADING = "GameCube"
    TILE_ICON = 32 * TILE_SCALE + 24

    def __init__(self, master, dialog):
        super().__init__(master)
        self.dialog = dialog
        self.locs = []
        self.slot = [None, None]          # Location per side
        self.problems = [[], []]
        self.warned = False

    # ---- data ------------------------------------------------------------------
    def reload(self):
        self.locs = gcfolder.find_locations(self.dialog.gc_root, self.dialog.exports_dir)
        if self.slot[0] is None and self.slot[1] is None:
            self._default_slots()
        else:                              # keep the choices, refreshed from the new list
            by_path = {l.path: l for l in self.locs}
            self.slot = [by_path.get(s.path, s) if s else None for s in self.slot]
        for i, loc in enumerate(self.slot):
            self.items[i], self.error[i], self.problems[i] = [], "", []
            if loc is None:
                continue
            try:
                files, problems = gcfolder.list_folder(loc.path)
            except OSError as ex:
                self.error[i] = f"Can't read {loc.path}: {ex}"
                continue
            self.items[i] = [GCItem(f) for f in files]
            titles = getattr(self.dialog.app, "titles", None)
            for it in self.items[i]:
                if it.empty and titles is not None:
                    try:
                        name = titles.lookup("gc", it.file.save.game_id)
                    except Exception:  # noqa - a name is a nicety
                        name = ""
                    if name:
                        it.title = f"Empty save · {name}"
            self.problems[i] = problems
        self.clamp()
        self.photos = {}
        self.redraw(full=True)

    def _default_slots(self):
        cards = [l for l in self.locs if l.kind == "card"]
        exports = next((l for l in self.locs if l.kind == "exports"), None)
        if not cards:
            self.slot = [exports, None]
            return
        counts = {l.path: len(gcfolder.list_folder(l.path)[0]) for l in cards if l.exists}
        best = max(cards, key=lambda l: (counts.get(l.path, -1), l.slot == "Card A"))
        twin = next((l for l in cards if l.region == best.region and l.slot != best.slot), None)
        first, second = (best, twin) if best.slot == "Card A" else (twin, best)
        if second is None or counts.get(second.path, 0) == 0:
            second = exports
        self.slot = [first or best, second]

    def frame_photo(self, it, idx, scale):
        key = (it.name, id(it), idx, scale)
        ph = self.photos.get(key)
        if ph is None and it.frames:
            img = it.frames[min(idx, len(it.frames) - 1)][0]
            ph = self._photo_of(img, scale)
            self.photos[key] = ph
        return ph

    def banner_photo(self, it, scale):
        key = (it.name, id(it), "banner", scale)
        if key not in self.photos:
            self.photos[key] = self._photo_of(it.banner, scale) if it.banner else None
        return self.photos[key]

    def _photo_of(self, icon, scale):
        if icon is None:
            return None
        if Image is not None:
            im = Image.frombytes("RGBA", (icon.width, icon.height), icon.data)
            im = im.resize((icon.width * scale, icon.height * scale), Image.NEAREST)
            return tk.PhotoImage(master=self.canvas, data=_png(im))
        import saveinfo
        ph = tk.PhotoImage(master=self.canvas, data=saveinfo.png_bytes(icon.data, icon.width, icon.height))
        return ph.zoom(scale) if scale > 1 else ph

    # ---- hooks -----------------------------------------------------------------
    def tab_label(self, side):
        loc = self.slot[side]
        return f"Slot {side + 1} · {loc.label if loc else 'empty'}"

    def slot_present(self, side):
        return self.slot[side] is not None

    def empty_text(self, side):
        loc = self.slot[side]
        if loc is not None and loc.kind == "exports":
            return "No exported saves yet.\nExport saves from a card to keep copies here."
        if loc is not None and not loc.exists:
            return f"{loc.label} isn't set up in Dolphin yet.\nCopy saves here to create it."
        return "This card has no saves."

    def space_text(self, side):
        loc = self.slot[side]
        if loc is None:
            return ""
        files = [it.file for it in self.items[side]]
        n = len(files)
        return f"{n} save{'s' if n != 1 else ''} · {gcfolder.blocks_used(files)} blocks"

    def mark_key(self, it):
        return it.name

    def tile_title(self, it):
        return it.title

    def tile_image(self, it):
        return self.frame_photo(it, it.frame_at(self.frame), TILE_SCALE)

    def marked_text(self, n):
        blocks = sum(it.file.save.blocks for it in self.items[self.side] if it.name in self.marks[self.side])
        return f"{n} marked · {blocks} blocks"

    def footer(self):
        return [("A", "Options"), ("X", "Mark"), ("Y", "Menu"), ("B", "Back"), ("LB/RB", "Slot")]

    def on_view(self):
        self.main_menu()

    def draw_info(self, it, box):
        c = self.canvas
        px0, py0, px1, py1 = box
        panel = self.panel_image(("gc", it.name, id(it)), self._corners(it), px1 - px0, py1 - py0)
        if panel:
            c.create_image(px0, py0, image=panel, anchor="nw", tags="ui")
        y = py0 + 20
        scale = 3 if (px1 - px0) >= 96 * 3 + 32 else 2
        ban = self.banner_photo(it, scale)
        if ban:
            c.create_image((px0 + px1) // 2, y, image=ban, anchor="n", tags="ui")
            y += 32 * scale + 18
        c.create_image(px0 + 16, y, anchor="nw", tags=("ui", "big"))
        self._update_big()
        tx = px0 + 16 + 32 * BIG_SCALE + 14
        tw = px1 - tx - 16
        c.create_text(tx, y + 4, text=it.title, fill="#e9edff", font=self.f_title, anchor="nw", width=tw, tags="ui")
        ty = y + 8 + self.f_title.metrics("linespace") * (2 if self.f_title.measure(it.title) > tw else 1)
        if it.detail:
            c.create_text(tx, ty, text=it.detail, fill="#e9edff", font=self.f_body, anchor="nw", width=tw, tags="ui")
        y += 32 * BIG_SCALE + 16
        s, f = it.file.save, it.file
        flags = [t for t, on in (("No copy", s.no_copy), ("No move", s.no_move)) if on]
        self.text_rows(px0 + 16, y, [
            f"{s.blocks} block{'s' if s.blocks != 1 else ''}  ·  {s.game_id}" + (f"  ·  {f.region}" if f.region else ""),
            f"{f.modified:%Y-%m-%d  %H:%M}" if f.modified else "",
            f.name, " · ".join(flags)], px1 - px0 - 32)

    def _corners(self, it):
        """Panel colours from the save's own banner (or icon), darkened."""
        src = it.banner or (it.frames[0][0] if it.frames else None)
        if src is None or Image is None:
            return [(40, 40, 70), (30, 30, 60), (20, 20, 45), (10, 10, 30)]
        im = Image.frombytes("RGBA", (src.width, src.height), src.data).convert("RGB").resize((2, 2), Image.BOX)
        return [tuple(int(v * 0.55) for v in px) for px in im.getdata()]

    def _update_big(self):
        it = self.current()
        if it is None or not self.canvas.find_withtag("big"):
            return
        ph = self.frame_photo(it, it.frame_at(self.frame), BIG_SCALE) or self.placeholder(32 * BIG_SCALE)
        if ph:
            self.canvas.itemconfigure("big", image=ph)

    def on_tick(self):
        items = self.items[self.side]
        for i, it in enumerate(items):           # animate the visible tiles
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
            "Close Dolphin first", "Dolphin writes its memory card folders while a game runs, so changes "
            "made now could be undone. Make sure Dolphin is closed, then choose OK.")
        self.warned = ok
        return ok

    def _conflict(self, f, old):
        where = self.slot[1 - self.side].label
        ans = self.prompts.choose(
            "Already on card", f"“{f.save.title}” ({f.save.detail or f.save.filename}) is already on {where}."
            + (f"\n\nThe one there was saved {old.modified:%Y-%m-%d %H:%M}; this one "
               f"{f.modified:%Y-%m-%d %H:%M}." if old.modified and f.modified else ""),
            [("replace", "Replace it (the old one is kept as a safety copy)"), ("skip", "Skip it"),
             ("cancel", "Cancel")], "cancel")
        return ans

    def _region_ok(self, files, dest):
        if dest.kind != "card":
            return True
        other = sorted({f.region for f in files if f.region and f.region != dest.region})
        if not other:
            return True
        return self.prompts.askyesno(
            "Different region", f"{'These saves are' if len(files) > 1 else 'This save is'} for "
            f"{' / '.join(other)} games, and {dest.label} is used by {dest.region} games, so Dolphin "
            "won't show them there. Copy anyway?")

    def _transfer(self, move):
        files = [it.file for it in self.selection()]
        dest = self.slot[1 - self.side]
        if not files or dest is None or not self._close_dolphin() or not self._region_ok(files, dest):
            return
        try:
            rep = gcfolder.transfer(files, dest.path, move, self.dialog.backup_dir, self._conflict)
        except (OSError, gcfolder.GCError) as ex:
            self.prompts.showerror("Couldn't finish", str(ex))
            rep = None
        self._report(rep, "Moved" if move else "Copied", dest.label)

    def _report(self, rep, verb, where=""):
        self.marks[self.side].clear()
        self.reload()
        if rep is None:
            return
        if rep.refused:
            lines = [f"• {f.save.title} ({f.save.detail or f.name}): {why}" for f, why in rep.refused]
            self.prompts.showinfo("Some saves weren't changed", "\n".join(lines))
        n = len(rep.done)
        if n:
            self.toast(f"{verb} {n} save{'s' if n != 1 else ''}" + (f" to {where}" if where else ""))
        elif rep.skipped and not rep.refused:
            self.toast("Nothing to do: the saves are already there")

    def _delete(self):
        files = [it.file for it in self.selection()]
        if not files or not self._close_dolphin():
            return
        what = f"{len(files)} saves" if len(files) > 1 else f"“{files[0].save.title}” ({files[0].save.detail or files[0].name})"
        if not self.prompts.askyesno("Delete", f"Delete {what} from {self.slot[self.side].label}?\n\n"
                                     "A safety copy is kept in the backups' _gc_card_safety folder."):
            return
        try:
            rep = gcfolder.delete(files, self.dialog.backup_dir)
        except OSError as ex:
            self.prompts.showerror("Couldn't delete", str(ex))
            rep = None
        self._report(rep, "Deleted")

    def _export(self):
        files = [it.file for it in self.selection()]
        try:
            rep = gcfolder.export(files, self.dialog.exports_dir)
        except (OSError, gcfolder.GCError) as ex:
            self.prompts.showerror("Couldn't export", str(ex))
            return
        self.marks[self.side].clear()
        n = len(rep.done)
        self.toast(f"Exported {n} save{'s' if n != 1 else ''} to {self.dialog.exports_dir}" if n
                   else "Already exported")
        self.reload()

    def save_menu(self):
        sel = self.selection()
        if not sel:
            return
        here, there = self.slot[self.side], self.slot[1 - self.side]
        what = f"{len(sel)} saves" if len(sel) > 1 else f"{sel[0].title}" + (f" — {sel[0].detail}" if sel[0].detail else "")
        opts = []
        if there is not None and there.path != here.path:
            opts += [("copy", f"Copy to {there.label}"), ("move", f"Move to {there.label}")]
        if any(it.empty for it in sel):
            opts.insert(0, ("empty", "Put back a kept copy…"))
        opts.append(("delete", "Delete"))
        if here.kind != "exports":
            opts.append(("export", "Export a copy to the backups"))
        opts += [("details", "Details"), ("cancel", "Cancel")]

        def chosen(key):
            {"copy": lambda: self._transfer(False), "move": lambda: self._transfer(True),
             "delete": self._delete, "export": self._export, "details": self.details,
             "empty": self.find_copies}.get(key, lambda: None)()
        self.show_overlay(what, "", opts, chosen)

    def find_copies(self):
        """Empty saves on this card: open Empty saves for the GameCube folder, to put a copy back."""
        from ui_emptysaves import EmptySavesDialog
        d = self.dialog
        emu = getattr(d, "emu", "Dolphin")
        dlg = EmptySavesDialog(d.app, locations=[(emu, "GameCube Saves", d.gc_root, ())])
        dlg.bind("<Destroy>", lambda e: self.reload() if e.widget is dlg else None, add="+")

    def details(self):
        it = self.current()
        if it is None:
            return
        s, f = it.file.save, it.file
        if f.empty:
            self.show_overlay(it.title, f"File: {f.path}\n\nThis file is empty (0 bytes), so the save's "
                              "data is gone. Use 'Put back a kept copy…' to restore it from a copy the "
                              "Save Manager kept, if there is one.", [("ok", "Close")], lambda k: None)
            return
        lines = [f"Game ID: {s.game_id}" + (f" ({f.region})" if f.region else ""),
                 f"Save name on the card: {s.filename}", f"File: {f.path}",
                 f"Size: {s.blocks} blocks ({f.size:,} bytes)"]
        if f.modified:
            lines.append(f"Saved: {f.modified:%Y-%m-%d %H:%M} (card time)")
        lines.append(f"Icon: {len(s.frames)} frame{'s' if len(s.frames) != 1 else ''}"
                     + (", plays back and forth" if s.bounce and len(s.frames) > 2 else ""))
        flags = [t for t, on in (("no copy", s.no_copy), ("no move", s.no_move)) if on]
        lines.append("The game marked it: " + ", ".join(flags) if flags else "Can be copied and moved")
        self.show_overlay(it.title + (f" — {it.detail}" if it.detail else ""), "\n".join(lines),
                          [("ok", "Close")], lambda k: None)

    def card_menu(self, side):
        if not self.locs:
            self.show_overlay(f"Slot {side + 1}", "Dolphin's GameCube folder wasn't found. Set it with "
                              "'Locations…' in the main window.", [("ok", "OK")], lambda k: None)
            return
        cur = self.slot[side]
        opts = []
        for k, l in enumerate(self.locs):
            n = len(gcfolder.list_folder(l.path)[0]) if l.exists else 0
            mark = "● " if cur is not None and l.path == cur.path else ""
            info = f"{n} save{'s' if n != 1 else ''}" if l.exists else "not set up yet"
            opts.append((str(k), f"{mark}{l.label}  ·  {info}"))
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
                    loc.path.mkdir(parents=True, exist_ok=True)
                    opener = getattr(self.dialog.app, "open_folder", None)
                    if opener:
                        opener(loc.path)
            elif key == "close":
                self.dialog.destroy()
        self.show_overlay("GameCube Memory Card Manager", "", opts, chosen)


def _png(im):
    import io
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class GCCardDialog(tk.Toplevel):
    def __init__(self, app, emu="Dolphin"):
        super().__init__(app)
        self.app = app
        self.title("GameCube Memory Card Manager")
        emu = emu if emu in DOLPHIN_EMUS else "Dolphin"
        self.emu = emu
        try:
            self.gc_root = app.resolve(emu).get("GameCube Saves")
        except Exception:  # noqa
            self.gc_root = None
        self.backup_dir = Path(app.backup_dir)
        self.exports_dir = self.backup_dir / "GameCube exports"
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if sh <= 820:
            self.geometry(f"{sw}x{sh - 40}+0+0")
        else:
            self.geometry("1200x740")
        self.minsize(820, 520)
        self.view = GCConsoleView(self, self)
        self.view.pack(fill="both", expand=True)
        self.msg = self.view.prompts
        self.after(10, self.view.start)
