"""
ui_ps1cards.py - the PS1 Memory Card Manager: a console-style, controller-friendly window for
PlayStation memory card files (DuckStation's .mcd cards, RetroArch's .srm cards, …) and an
"Exported saves" folder of .mcs files in the backups.

Each save shows its own animated icon, its title, the game it belongs to (from the app's game
name lookup), its region and a map of the card's 15 blocks. Copies, moves, deletes and exports go
through ps1card.py (verified writes, the card kept as it was before every change, deletes that
can be recovered). Built on ui_console.ConsoleBase.
"""
from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path

import ps1card
from ui_console import ConsoleBase, Image, ACCENT

TILE_SCALE, BIG_SCALE = 4, 7          # PS1 icons are 16x16
TICKS_PER_FRAME = 4                   # about 4 frames a second


def _png(im):
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class PS1Item:
    def __init__(self, save: ps1card.PS1Save, game: str = ""):
        self.save = save
        self.name = save.name
        self.title = save.title or save.name
        self.game = game
        self.frames = save.frames

    def frame_at(self, tick):
        return (tick // TICKS_PER_FRAME) % len(self.frames) if self.frames else 0


class PS1ConsoleView(ConsoleBase):
    HEADING = "Memory Card"
    SUBHEADING = "PlayStation"
    TILE_ICON = 16 * TILE_SCALE + 24

    def __init__(self, master, dialog):
        super().__init__(master)
        self.dialog = dialog
        self.cards = []                   # [(label, path)]
        self.slot = [None, None]          # ("card", path) or ("exports", path)
        self.stores = [None, None]
        self.warned = False

    # ---- data ------------------------------------------------------------------
    def reload(self):
        self.cards = ps1card.find_cards(self.dialog.card_folders())
        if self.slot == [None, None]:
            self._default_slots()
        for i, slot in enumerate(self.slot):
            self.items[i], self.error[i], self.stores[i] = [], "", None
            if slot is None:
                continue
            kind, path = slot
            try:
                store = ps1card.CardStore(path, self._label(slot)) if kind == "card" else \
                    ps1card.FolderStore(path)
            except ps1card.PS1Error as ex:
                self.error[i] = f"Can't read {Path(path).name}: {ex}"
                continue
            self.stores[i] = store
            self.items[i] = [PS1Item(s, self._game(s)) for s in store.saves()]
        self.clamp()
        self.photos = {}
        self.redraw(full=True)

    def _game(self, s):
        titles = getattr(self.dialog.app, "titles", None)
        if titles is not None and s.product:
            try:
                return titles.lookup("ps1", s.product)
            except Exception:  # noqa
                return ""
        return ""

    def _default_slots(self):
        exports = ("exports", self.dialog.exports_dir)
        if not self.cards:
            self.slot = [exports, None]
            return
        by_time = sorted(self.cards, key=lambda c: c[1].stat().st_mtime, reverse=True)
        first = by_time[0][1]
        shared = next((p for _l, p in self.cards if p.name.lower().startswith("shared_card") and p != first), None)
        other = shared or (by_time[1][1] if len(by_time) > 1 else None)
        self.slot = [("card", first), ("card", other) if other else exports]

    def _label(self, slot):
        if slot is None:
            return "empty"
        kind, path = slot
        if kind == "exports":
            return "Exported saves"
        return next((l for l, p in self.cards if p == path), Path(path).name)

    def _short(self, slot):
        if slot is None:
            return "empty"
        if slot[0] == "exports":
            return "Exported saves"
        stem = Path(slot[1]).stem
        return stem if len(stem) <= 26 else stem[:24].rstrip(" -_(") + "…"

    def frame_photo(self, it, idx, scale):
        key = (it.name, id(it), idx, scale)
        if key not in self.photos:
            ph = None
            if it.frames:
                icon = it.frames[min(idx, len(it.frames) - 1)]
                if Image is not None:
                    im = Image.frombytes("RGBA", (16, 16), icon.data).resize((16 * scale, 16 * scale), Image.NEAREST)
                    ph = tk.PhotoImage(master=self.canvas, data=_png(im))
                else:
                    import saveinfo
                    ph = tk.PhotoImage(master=self.canvas, data=saveinfo.png_bytes(icon.data, 16, 16)).zoom(scale)
            self.photos[key] = ph
        return self.photos[key]

    # ---- hooks -----------------------------------------------------------------
    def tab_label(self, side):
        return f"Slot {side + 1} · {self._short(self.slot[side])}"

    def slot_present(self, side):
        return self.slot[side] is not None

    def empty_text(self, side):
        slot = self.slot[side]
        if slot is not None and slot[0] == "exports":
            return "No exported saves yet.\nExport saves from a card to keep copies here."
        return "This card has no saves."

    def space_text(self, side):
        store = self.stores[side]
        if store is None:
            return ""
        free = store.free()
        n = len(self.items[side])
        s = f"{n} save{'s' if n != 1 else ''}"
        return s + (f" · {free} of 15 blocks free" if free is not None else "")

    def mark_key(self, it):
        return it.name

    def tile_title(self, it):
        return it.game or it.title

    def tile_image(self, it):
        return self.frame_photo(it, it.frame_at(self.frame), TILE_SCALE)

    def marked_text(self, n):
        blocks = sum(it.save.n_blocks for it in self.items[self.side] if it.name in self.marks[self.side])
        return f"{n} marked · {blocks} block{'s' if blocks != 1 else ''}"

    def on_view(self):
        self.main_menu()

    def draw_info(self, it, box):
        c = self.canvas
        px0, py0, px1, py1 = box
        panel = self.panel_image(("ps1", it.name, id(it)), self._corners(it), px1 - px0, py1 - py0)
        if panel:
            c.create_image(px0, py0, image=panel, anchor="nw", tags="ui")
        y = py0 + 20
        c.create_image((px0 + px1) // 2, y, anchor="n", tags=("ui", "big"))
        self._update_big()
        y += 16 * BIG_SCALE + 18
        tw = px1 - px0 - 32
        c.create_text(px0 + 16, y, text=it.game or it.title, fill="#e9edff", font=self.f_title,
                      anchor="nw", width=tw, tags="ui")
        y += self.f_title.metrics("linespace") * (2 if self.f_title.measure(it.game or it.title) > tw else 1) + 4
        if it.game:
            c.create_text(px0 + 16, y, text=it.title, fill="#e9edff", font=self.f_body, anchor="nw",
                          width=tw, tags="ui")
            y += self.f_body.metrics("linespace") + 6
        s = it.save
        y = self.text_rows(px0 + 16, y, [
            f"{s.n_blocks} block{'s' if s.n_blocks != 1 else ''}" + (f"  ·  {s.product}" if s.product else "")
            + (f"  ·  {s.region}" if s.region else ""), s.name,
            f"Problem: {s.problem}" if s.problem else ""], tw) + 10
        if self.stores[self.side] is not None and self.stores[self.side].kind == "card":
            self._block_map(px0 + 16, y, px1 - 16, it)

    def _block_map(self, x0, y, x1, it):
        """The card's 15 blocks: this save's in the accent colour, others' dimmed, free ones empty."""
        c = self.canvas
        used = {b for other in self.items[self.side] for b in other.save.blocks}
        mine = set(it.save.blocks)
        cw = min(24, (x1 - x0 - 14 * 4) // 15)
        for b in range(1, 16):
            bx = x0 + (b - 1) * (cw + 4)
            fill = ACCENT if b in mine else ("#5b6694" if b in used else "")
            c.create_rectangle(bx, y, bx + cw, y + cw, outline="#8f9bd0", fill=fill, tags="ui")
        c.create_text(x0, y + cw + 6, text="Blocks on this card", fill="#c5cbea", font=self.f_small,
                      anchor="nw", tags="ui")

    def _corners(self, it):
        if not it.frames or Image is None:
            return [(40, 40, 70), (30, 30, 60), (20, 20, 45), (10, 10, 30)]
        im = Image.frombytes("RGBA", (16, 16), it.frames[0].data)
        bg = Image.new("RGBA", im.size, (30, 30, 60, 255))
        bg.alpha_composite(im)
        px = list(bg.convert("RGB").resize((2, 2), Image.BOX).getdata())
        return [tuple(int(v * 0.55) for v in p) for p in px]

    def _update_big(self):
        it = self.current()
        if it is None or not self.canvas.find_withtag("big"):
            return
        ph = self.frame_photo(it, it.frame_at(self.frame), BIG_SCALE) or self.placeholder(16 * BIG_SCALE)
        if ph:
            self.canvas.itemconfigure("big", image=ph)

    def on_tick(self):
        if self.frame % TICKS_PER_FRAME == 0:
            for i, it in enumerate(self.items[self.side]):
                if len(it.frames) > 1:
                    ph = self.tile_image(it)
                    if ph is not None:
                        self.canvas.itemconfigure(f"tile{i}", image=ph)
            self._update_big()
        return False

    # ---- actions -----------------------------------------------------------------
    def _close_emulator(self):
        if self.warned:
            return True
        ok = self.prompts.askokcancel(
            "Close the emulator first", "DuckStation and RetroArch write the memory card while a game "
            "runs, so changes made now could be undone. Make sure they're closed, then choose OK.")
        self.warned = ok
        return ok

    def _conflict(self, s, old):
        where = self._label(self.slot[1 - self.side])
        return self.prompts.choose(
            "Already on card", f"“{s.title or s.name}” ({s.name}) is already on {where}.",
            [("replace", "Replace it (the card is kept as it was in the backups)"), ("skip", "Skip it"),
             ("cancel", "Cancel")], "cancel")

    def _report(self, rep, verb, where=""):
        self.marks[self.side].clear()
        self.reload()
        if rep is None:
            return
        if rep.refused:
            lines = [f"• {s.title or s.name}: {why}" for s, why in rep.refused]
            self.prompts.showinfo("Some saves weren't changed", "\n".join(lines))
        n = len(rep.done)
        if n:
            self.toast(f"{verb} {n} save{'s' if n != 1 else ''}" + (f" to {where}" if where else ""))
        elif rep.skipped and not rep.refused:
            self.toast("Nothing to do: the saves are already there")

    def _transfer(self, move, dest_side=None, dest_store=None, verb=None):
        saves = [it.save for it in self.selection()]
        src = self.stores[self.side]
        dst = dest_store or self.stores[1 - self.side if dest_side is None else dest_side]
        if not saves or src is None or dst is None:
            return
        touches_card = src.kind == "card" and move or dst.kind == "card"
        if touches_card and not self._close_emulator():
            return
        try:
            rep = ps1card.transfer(src, saves, dst, move, self.dialog.backup_dir, self._conflict)
        except (OSError, ps1card.PS1Error) as ex:
            self.prompts.showerror("Couldn't finish", str(ex))
            rep = None
        self._report(rep, verb or ("Moved" if move else "Copied"), dst.label)

    def _export(self):
        exports = ps1card.FolderStore(self.dialog.exports_dir)
        self._transfer(False, dest_store=exports, verb="Exported")

    def _delete(self):
        saves = [it.save for it in self.selection()]
        store = self.stores[self.side]
        if not saves or store is None:
            return
        if store.kind == "card" and not self._close_emulator():
            return
        what = f"{len(saves)} saves" if len(saves) > 1 else f"“{saves[0].title or saves[0].name}”"
        note = ("It's marked deleted on the card, as the console does, and the card is kept as it was "
                "in the backups' _ps1_card_safety folder.") if store.kind == "card" else \
            "A safety copy is kept in the backups' _ps1_card_safety folder."
        if not self.prompts.askyesno("Delete", f"Delete {what} from {self._label(self.slot[self.side])}?\n\n{note}"):
            return
        try:
            rep = ps1card.delete(store, saves, self.dialog.backup_dir)
        except (OSError, ps1card.PS1Error) as ex:
            self.prompts.showerror("Couldn't delete", str(ex))
            rep = None
        self._report(rep, "Deleted")

    def save_menu(self):
        sel = self.selection()
        if not sel:
            return
        here, there = self.slot[self.side], self.slot[1 - self.side]
        what = f"{len(sel)} saves" if len(sel) > 1 else (sel[0].game or sel[0].title)
        opts = []
        if there is not None and self.stores[1 - self.side] is not None and there != here:
            opts += [("copy", f"Copy to {self._short(there)}"), ("move", f"Move to {self._short(there)}")]
        opts.append(("delete", "Delete"))
        if here[0] == "card" and (there is None or there[0] != "exports"):
            opts.append(("export", "Export a copy to the backups"))
        opts += [("details", "Details"), ("cancel", "Cancel")]

        def chosen(key):
            {"copy": lambda: self._transfer(False), "move": lambda: self._transfer(True),
             "delete": self._delete, "export": self._export, "details": self.details}.get(key, lambda: None)()
        self.show_overlay(what, "", opts, chosen)

    def details(self):
        it = self.current()
        if it is None:
            return
        s = it.save
        lines = [f"Save name on the card: {s.name}"]
        if s.product:
            lines.append(f"Game: {s.product}" + (f" ({s.region})" if s.region else ""))
        lines.append(f"Size: {s.n_blocks} block{'s' if s.n_blocks != 1 else ''} ({s.size:,} bytes)")
        if s.blocks:
            lines.append("Blocks: " + ", ".join(str(b) for b in s.blocks))
        if s.path:
            lines.append(f"File: {s.path}")
        lines.append(f"Icon: {len(s.frames)} frame{'s' if len(s.frames) != 1 else ''}")
        if s.problem:
            lines.append(f"Problem: {s.problem} (it won't be copied)")
        self.show_overlay((it.game + " — " if it.game else "") + it.title, "\n".join(lines),
                          [("ok", "Close")], lambda k: None)

    def card_menu(self, side):
        cur = self.slot[side]
        opts = []
        for k, (label, path) in enumerate(self.cards):
            try:
                store = ps1card.CardStore(path)
                info = f"{len(store.saves())} saves · {store.free()} blocks free"
            except ps1card.PS1Error:
                info = "can't be read"
            mark = "● " if cur == ("card", path) else ""
            opts.append((f"c{k}", f"{mark}{label}  ·  {info}"))
        mark = "● " if cur is not None and cur[0] == "exports" else ""
        opts.append(("exports", f"{mark}Exported saves (backups)"))
        opts += [("new", "Create a new blank card"), ("cancel", "Cancel")]

        def chosen(key):
            if key.startswith("c") and key[1:].isdigit():
                self.slot[side] = ("card", self.cards[int(key[1:])][1])
            elif key == "exports":
                self.slot[side] = ("exports", self.dialog.exports_dir)
            elif key == "new":
                self.new_card(side)
                return
            else:
                return
            self.side = side
            self.cursor[side] = 0
            self.reload()
        self.show_overlay(f"Choose what's in slot {side + 1}", "", opts, chosen)

    def new_card(self, side):
        folder = self.dialog.new_card_folder()
        if folder is None:
            self.prompts.showinfo("New card", "DuckStation's memory card folder wasn't found. Set it with "
                                  "'Locations…' in the main window.")
            return
        try:
            path = ps1card.new_card(folder)
        except (OSError, ps1card.PS1Error) as ex:
            self.prompts.showerror("New card", str(ex))
            return
        self.slot[side] = ("card", path)
        self.side = side
        self.cursor[side] = 0
        self.reload()
        self.toast(f"Created {path.name} in DuckStation's memory card folder")

    def main_menu(self):
        opts = [("slot1", "Choose slot 1…"), ("slot2", "Choose slot 2…"), ("new", "Create a new blank card"),
                ("refresh", "Refresh"), ("folder", "Open this card's folder"), ("close", "Close manager"),
                ("cancel", "Cancel")]

        def chosen(key):
            if key == "slot1":
                self.card_menu(0)
            elif key == "slot2":
                self.card_menu(1)
            elif key == "new":
                self.new_card(self.side)
            elif key == "refresh":
                self.reload()
            elif key == "folder":
                slot = self.slot[self.side]
                if slot is not None:
                    folder = Path(slot[1]) if slot[0] == "exports" else Path(slot[1]).parent
                    folder.mkdir(parents=True, exist_ok=True)
                    opener = getattr(self.dialog.app, "open_folder", None)
                    if opener:
                        opener(folder)
            elif key == "close":
                self.dialog.destroy()
        self.show_overlay("PS1 Memory Card Manager", "", opts, chosen)


class PS1CardDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("PS1 Memory Card Manager")
        self.backup_dir = Path(app.backup_dir)
        self.exports_dir = self.backup_dir / "PS1 exports"
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh - 40}+0+0" if sh <= 820 else "1200x740")
        self.minsize(820, 520)
        self.view = PS1ConsoleView(self, self)
        self.view.pack(fill="both", expand=True)
        self.msg = self.view.prompts
        self.after(10, self.view.start)

    def _resolved(self, emu, label):
        try:
            return self.app.resolve(emu).get(label)
        except Exception:  # noqa
            return None

    def card_folders(self):
        return [("DuckStation", self._resolved("DuckStation", "Memory Cards")),
                ("RetroArch", self._resolved("RetroArch", "Saves"))]

    def new_card_folder(self):
        return self._resolved("DuckStation", "Memory Cards")
