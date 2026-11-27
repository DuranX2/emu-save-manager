"""libpad.py - controller navigation for the ROM Library window (same buttons as the main window).

  Up / Down        move in the systems list, or through the games (covers: one row at a time)
  Left / Right     covers: move along the row (Left at the start of a row goes to the systems);
                   list view: switch between the systems and the games
  A                systems: go to the games · games: the game's actions (check out, check in,
                   add to library, favourite, details, find the right artwork)
  B                back to the systems list
  X                select several games (move, then X on each)
  LB / RB          previous / next system, from anywhere
  LT               games: the game's actions (as A, and as a right-click) · systems: the system's
                   actions (look up its artwork, leave it out of artwork look-ups, Check-up, BIOS)
  Right stick      scroll the games or the systems list (the cursor stays where it is)
  Y                the Library's menu: Refresh, Check-up, BIOS, Artwork, Settings, Show filter,
                   covers / list
  View             covers / list

Other windows and questions (Settings, Check-up, "Check in … ?") work as in the main window.
"""
from __future__ import annotations

import mainpad


class LibraryPad(mainpad.PadBase):
    HINTS = {"systems": "A games · LT system menu · Y menu",
             "games": "A actions · X select · Y menu"}

    def __init__(self, app, backend="auto"):
        super().__init__(app, "games", backend=backend)

    # ---- where the cursor is ---------------------------------------------------------
    def covers(self):
        return self.app.view == "covers"

    def focus(self, zone):
        if zone != self.zone:
            self.marking = False
        self.zone = zone
        a = self.app
        if zone == "systems":
            a.sys_tree.focus_set()
            if not a.sys_tree.selection() and a.sys_tree.get_children():
                a.sys_tree.selection_set(a.sys_tree.get_children()[0])
        elif self.covers():
            g = a.grid_view
            g.canvas.focus_set()
            if not g.sel and g.keys:
                g.set_selection([g.keys[0]])
                g.see(g.keys[0])
        else:
            a.tree.focus_set()
            if not a.tree.selection():
                kids = a.tree.get_children()
                if kids:
                    a.tree.selection_set(kids[0])
                    a.tree.focus(kids[0])
                    a.tree.see(kids[0])
        self.show_hint()

    def _cursor(self):
        """The game the cursor is on (covers: the grid's anchor; list: the tree's focus)."""
        a = self.app
        if self.covers():
            g = a.grid_view
            return g.anchor if g.anchor in g.keys else (g.sel[-1] if g.sel else None)
        return a.tree.focus() or (a.tree.selection() or (None,))[0]

    def _game_rows(self):
        a = self.app
        return list(a.grid_view.keys) if self.covers() else list(a.tree.get_children())

    def _go_to(self, key):
        """Move the cursor to a game; it's selected unless several are being marked."""
        a = self.app
        if self.covers():
            g = a.grid_view
            if self.marking:
                g.anchor = key
                g.draw()
            else:
                g.set_selection([key])
            g.see(key)
        else:
            if not self.marking:
                a.tree.selection_set(key)
            a.tree.focus(key)
            a.tree.see(key)
        if self.marking:
            self._mark_status()

    # ---- D-pad -------------------------------------------------------------------------
    def _step(self, step):
        rows = self._game_rows()
        if not rows:
            return
        cur = self._cursor()
        i = rows.index(cur) if cur in rows else 0
        self._go_to(rows[max(0, min(len(rows) - 1, i + step))])

    def _up(self):
        if self.zone == "systems":
            self._system(-1, follow=False)
        else:
            self._step(-self.app.grid_view.cols if self.covers() else -1)

    def _down(self):
        if self.zone == "systems":
            self._system(1, follow=False)
        else:
            self._step(self.app.grid_view.cols if self.covers() else 1)

    def _left(self):
        if self.zone == "games" and self.covers():
            rows = self._game_rows()
            cur = self._cursor()
            i = rows.index(cur) if cur in rows else 0
            if i % max(1, self.app.grid_view.cols) != 0:
                self._step(-1)
                return
        self.focus("systems")

    def _right(self):
        if self.zone == "systems":
            self.focus("games")
        elif self.covers():
            self._step(1)

    def _back(self):
        if self.zone == "games":
            self.focus("systems")

    # ---- systems -------------------------------------------------------------------------
    def _system(self, step, follow=True):
        t = self.app.sys_tree
        rows = list(t.get_children())
        if not rows:
            return
        cur = (t.selection() or (rows[0],))[0]
        i = rows.index(cur) if cur in rows else 0
        new = rows[max(0, min(len(rows) - 1, i + step))]
        if new != cur:
            t.selection_set(new)
            t.see(new)
            self.app._on_system()
        if follow and self.zone == "games":
            self.marking = False
            self.app.after(50, lambda: self.focus("games"))

    def _prev(self):
        self._system(-1)

    def _next(self):
        self._system(1)

    # ---- buttons -------------------------------------------------------------------------
    def _ok(self):
        if self.zone == "systems":
            self.focus("games")
            return
        keys = self.app.selected()
        if not keys:
            cur = self._cursor()
            if cur:
                self._go_to(cur)
                keys = self.app.selected()
        if keys:
            self.open_menu(self._menu_title(keys), self.game_items(keys))

    def _context(self):
        if self.zone == "games":
            self._ok()
            return
        s = self.app.system_sel
        items = system_items(self.app, s)
        if items:
            self.open_menu(system_title(s), items)

    def _mark(self):
        if self.zone != "games":
            return
        a = self.app
        cur = self._cursor()
        if not cur:
            return
        if self.covers():
            g = a.grid_view
            if not self.marking:                  # the first X keeps the game it's on and starts marking
                sel = [cur]
            else:
                sel = list(g.sel)
                if cur in sel:
                    sel.remove(cur)
                else:
                    sel.append(cur)
            g.sel = sel
            g.anchor = cur
            g.draw()
            a.on_grid_select()
            self.marking = bool(sel)
        else:
            if not self.marking:
                a.tree.selection_set(cur)
            elif cur in a.tree.selection():
                a.tree.selection_remove(cur)
            else:
                a.tree.selection_add(cur)
            self.marking = bool(a.tree.selection())
        self._mark_status()

    def _mark_status(self):
        a = self.app
        n = len(a.selected())
        cur = self._cursor()
        here = a.lib.get(cur).title if cur and a.lib and a.lib.get(cur) else ""
        a.set_status(f"{n} selected · at: {here}   (X: select/unselect, A: actions)" if n else "Nothing selected")

    def _view(self):
        a = self.app
        a.set_view("list" if a.view == "covers" else "covers")
        self.marking = False
        self.focus(self.zone)

    def _menu(self):
        self.open_menu("ROM Library", self.library_items())

    # ---- menus ---------------------------------------------------------------------------
    def _menu_title(self, keys):
        return game_title(self.app, keys)

    def game_items(self, keys):
        return game_items(self.app, keys)

    def library_items(self):
        items = library_items(self.app)
        return [(t, self._view if f == "view" else (lambda f=f[1]: self._filter(f)) if isinstance(f, tuple) else f)
                for t, f in items]

    def _filter(self, f):
        a = self.app
        a.v_filter.set(f)
        a.fill_games()


# ---------------------------------------------------------------------------
# The menus (shared by A / LT / Y and the right-click menus in ui_library)
# ---------------------------------------------------------------------------
def game_title(app, keys):
    if len(keys) == 1:
        return app.lib.get(keys[0]).title
    return f"{len(keys)} games"


def game_items(app, keys):
    """[(label, function)] for the selected games."""
    a, lib = app, app.lib
    sts = {lib.status(k)[0] for k in keys}
    items = []
    if sts & {"not_here", "partial"}:
        items.append(("⬇ Check out", a.check_out))
    if sts & {"here", "partial"}:
        items.append(("⬆ Check in", a.check_in))
    if "local_only" in sts:
        items.append(("Add to library", a.add_to_library))
    if any(k in lib.items for k in keys):
        fav = all(lib.is_favourite(k) for k in keys)
        items.append(("☆ Unfavourite" if fav else "★ Favourite", a.toggle_favourite))
    if len(keys) == 1:
        key = keys[0]
        items.append(("Details…", a.show_details))
        if a.can_match():
            def match(key=key):
                import ui_covers
                ui_covers.MatchDialog(a, key)
            items.append(("Wrong artwork? Find the right game…", match))
        if key in lib.items and getattr(a, "meta", None) is not None:
            items.append(("Use my own cover picture…", lambda: a.choose_picture(key, "cover")))
            items.append(("Use my own screenshot…", lambda: a.choose_picture(key, "screenshot")))
    return items


def system_title(system):
    import romlibrary as L
    return L.system_name(system)


def system_items(app, system):
    """[(label, function)] for one system in the systems list (none for "All systems")."""
    import ui_library as U
    if not app.lib or system == U.ALL:
        return []
    name = system_title(system)
    items = []
    if getattr(app, "meta", None) is not None:
        items.append((f"Look up artwork for {name}", lambda: app.scrape_system(system)))
        if system in app.scrape_skip():
            items.append((f"Include {name} in artwork look-ups again", lambda: app.toggle_scrape_skip(system)))
        else:
            items.append((f"Leave {name} out of artwork look-ups", lambda: app.toggle_scrape_skip(system)))
    items.append(("Check-up…", app.open_checkup))
    items.append(("BIOS…", app.open_bios))
    return items


def library_items(app):
    """The Y menu. "view" and ("filter", name) are filled in by the pad."""
    import ui_library as U
    a = app
    items = [("Refresh", a.refresh), ("Check-up…", a.open_checkup), ("BIOS…", a.open_bios),
             ("Artwork…", a.open_artwork), ("Settings…", a.open_settings), ("Open log folder", a.open_log),
             ("List view" if a.view == "covers" else "Covers view", "view")]
    if a.v_search.get():
        items.append(("Clear the search", lambda: a.v_search.set("")))
    for f in U.FILTERS:
        mark = "● " if a.v_filter.get() == f else "    "
        items.append((f"{mark}Show: {f}", ("filter", f)))
    return items
