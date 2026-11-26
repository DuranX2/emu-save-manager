"""mainpad.py - controller navigation for the Emulator Save Manager's main window.

The console-style managers already work with a controller (ui_console + gamepad). This gives the
main window the same: no extra packages, the same XInput / joystick backends.

  Up / Down      move in the focused list (emulators, saves, backups)
  Left / Right   switch between those lists
  A              emulators: go to the saves · saves: back up the selected · backups: restore
  B              back to the emulators list
  X              saves: add or remove the current save from the selection
  Y (menu)       an action menu: sync, back up, restore, Save History, Cloud Sync, the managers …
  LB / RB        previous / next emulator, from anywhere
  View           group saves by game on / off

The app's other windows and questions (backup note, "Restore … Continue?") work too: the
D-pad moves between their buttons and fields, A presses, B cancels. A window with its own
controller handling (the console-style views) is left to that handling.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import gamepad

ZONES = ("emulators", "saves", "backups")


class AppPad(gamepad.Navigator):
    """A Navigator that follows focus across the app's windows instead of one window."""

    def __init__(self, app, callback, backend="auto"):
        self.app = app
        super().__init__(app, callback, backend=backend, keys=False)

    def _focused(self):
        return focus_window(self.app) is not None


def focus_path(app) -> str:
    """The Tk path of the focused window in this app ('' if none). Works for Tk's own question
    boxes too, which aren't Python widgets."""
    try:
        return str(app.tk.call("focus"))
    except tk.TclError:
        return ""


def focus_window(app):
    """The path of the app's toplevel that has focus ('.' is the main window), or None when no
    window of the app has focus or another Navigator handles that window."""
    f = focus_path(app)
    if not f:
        return None
    try:
        top = str(app.tk.call("winfo", "toplevel", f))
    except tk.TclError:
        return None
    for nav in list(gamepad.Navigator.active):
        if isinstance(nav, AppPad) or not nav.running:
            continue
        try:
            if str(nav.widget.winfo_toplevel()) == top:
                return None
        except tk.TclError:
            continue
    return top


class MainPad:
    """Attach to the main window: MainPad(app). app needs emu_list, save_tree, backup_tree,
    on_select_emulator, backup_selected, backup_all, restore_selected and the open_* methods."""

    def __init__(self, app, backend="auto"):
        self.app = app
        self.zone = "emulators"
        self.menu = None                      # ActionMenu while open
        self.marking = False                  # X used in the saves: moving keeps the selection
        self.nav = AppPad(app, self.act, backend=backend)
        self.hint = None

    @property
    def connected(self):
        """A controller is plugged in (or, for a backend that can't tell, could be)."""
        b = self.nav.backend
        if b is None:
            return False
        present = getattr(b, "present", None)
        return bool(present()) if present else True

    def watch_hint(self, label, every_ms=2000):
        """Show the controller hints in `label` while a controller is connected."""
        self.hint = label
        shown = False

        def check():
            nonlocal shown
            if not self.nav.running:
                return
            now = self.connected
            try:
                if now and not shown:
                    label.pack(side="left", padx=(16, 0))
                    self.show_hint()
                elif shown and not now:
                    label.pack_forget()
                shown = now
                self.app.after(every_ms, check)
            except tk.TclError:
                pass                                  # window closed
        check()

    def stop(self):
        self.nav.stop()

    # ---- dispatch ---------------------------------------------------------------
    def act(self, action):
        top = focus_window(self.app)
        if top is None:
            return
        if top != str(self.app):
            dialog_action(self.app, top, action)
            return
        if self.menu is not None:
            self.menu.act(action)
            return
        getattr(self, "_" + action, lambda: None)()

    def widget(self, zone=None):
        return {"emulators": self.app.emu_list, "saves": self.app.save_tree,
                "backups": self.app.backup_tree}[zone or self.zone]

    def focus(self, zone):
        if zone != self.zone:
            self.marking = False
        self.zone = zone
        w = self.widget()
        w.focus_set()
        if zone != "emulators" and not w.selection():
            kids = w.get_children()
            if kids:
                w.selection_set(kids[0])
                w.focus(kids[0])
                w.see(kids[0])
        self.show_hint()

    def show_hint(self):
        hints = {"emulators": "A: saves   LB/RB: emulator   Y: menu",
                 "saves": "A: back up selected   X: select   B: emulators   Y: menu",
                 "backups": "A: restore   B: emulators   Y: menu"}
        if self.hint is not None:
            self.hint.config(text="🎮 " + hints[self.zone])

    # ---- actions ----------------------------------------------------------------
    def _up(self):
        self._move(-1)

    def _down(self):
        self._move(1)

    def _move(self, step):
        w = self.widget()
        if self.zone == "emulators":
            n = w.size()
            if not n:
                return
            cur = (w.curselection() or (0,))[0]
            new = max(0, min(n - 1, cur + step))
            if new != cur:
                w.selection_clear(0, "end")
                w.selection_set(new)
                w.activate(new)
                w.see(new)
                self.app.on_select_emulator()
            return
        rows = visible_rows(w)
        if not rows:
            return
        cur = w.focus() or (w.selection() or (rows[0],))[0]
        i = rows.index(cur) if cur in rows else 0
        new = rows[max(0, min(len(rows) - 1, i + step))]
        if not (self.zone == "saves" and self.marking):
            w.selection_set(new)
        w.focus(new)
        w.see(new)
        if self.zone == "saves" and self.marking:
            self._mark_status()

    def _left(self):
        i = ZONES.index(self.zone)
        self.focus(ZONES[max(0, i - 1)])

    def _right(self):
        i = ZONES.index(self.zone)
        self.focus(ZONES[min(len(ZONES) - 1, i + 1)])

    def _back(self):
        self.focus("emulators")

    def _ok(self):
        if self.zone == "emulators":
            self.focus("saves")
        elif self.zone == "saves":
            self.app.backup_selected()
        else:
            self.app.restore_selected()

    def _mark(self):
        if self.zone != "saves":
            return
        w = self.widget()
        cur = w.focus()
        if not cur:
            return
        if cur in w.selection():
            w.selection_remove(cur)
        else:
            w.selection_add(cur)
        self.marking = bool(w.selection())
        self._mark_status()

    def _mark_status(self):
        w = self.widget("saves")
        n = len(w.selection())
        here = w.item(w.focus(), "text") or (w.item(w.focus(), "values") or [""])[0] if w.focus() else ""
        if hasattr(self.app, "set_status"):
            self.app.set_status(f"{n} selected · at: {here}   (X: select/unselect, A: back up the selected)"
                                if n else "Nothing selected")

    def _prev(self):
        self._switch_emulator(-1)

    def _next(self):
        self._switch_emulator(1)

    def _switch_emulator(self, step):
        zone = self.zone
        self.zone = "emulators"
        self._move(step)
        self.zone = zone
        if zone != "emulators":
            self.focus(zone)

    def _view(self):
        sl = getattr(self.app, "save_list", None)
        if sl is not None:
            sl.grouped.set(not sl.grouped.get())
            self.app._toggle_grouping()

    def _menu(self):
        self.menu = ActionMenu(self.app, self.menu_items(), on_close=self._menu_closed)

    def _menu_closed(self):
        self.menu = None
        self.focus(self.zone)

    def menu_items(self):
        a = self.app
        emu = a.current or "this emulator"
        items = [("Sync now (both ways)", self.sync_now),
                 (f"Back up all {emu} saves", a.backup_all),
                 ("Back up the selected saves", a.backup_selected),
                 ("Restore the selected backup", a.restore_selected),
                 ("Save History…", a.open_history),
                 ("Cloud Sync…", a.open_cloud),
                 ("PS2 Card Manager…", a.open_ps2_cards),
                 ("PS1 Card Manager…", a.open_ps1_cards),
                 ("GameCube Card Manager…", a.open_gc_cards),
                 ("Wii Save Manager…", a.open_wii_saves),
                 ("PSP Save Manager…", a.open_psp_saves),
                 ("RetroArch Save Manager…", a.open_ra_saves),
                 ("ROM Library…", getattr(a, "open_library", None)),
                 ("BIOS Vault…", a.open_bios_vault)]
        return [(t, f) for t, f in items if f is not None]

    def sync_now(self):
        d = self.app.open_cloud()
        if d is not None:
            d.after(300, lambda: d.start("both"))


def visible_rows(tree):
    """Treeview rows in display order, children of open items included."""
    out = []

    def walk(parent):
        for iid in tree.get_children(parent):
            out.append(iid)
            if tree.item(iid, "open"):
                walk(iid)
    walk("")
    return out


# ---------------------------------------------------------------------------
# Other windows and questions
# ---------------------------------------------------------------------------
FOCUSABLE = {"Button", "TButton", "Checkbutton", "TCheckbutton", "Radiobutton", "TRadiobutton",
             "Entry", "TEntry", "TCombobox", "TSpinbox", "Spinbox", "Listbox", "Treeview"}
PRESSABLE = {"Button", "TButton", "Checkbutton", "TCheckbutton", "Radiobutton", "TRadiobutton"}


def dialog_action(app, top: str, action: str):
    """In another window or question: D-pad moves between its buttons and fields, A presses,
    B cancels. Paths are used throughout (Tk's question boxes aren't Python widgets)."""
    call = app.tk.call
    f = focus_path(app)
    try:
        if not f or str(call("winfo", "toplevel", f)) != top or f == top:
            f = first_focusable(app, top)
            if not f:
                return
            call("focus", f)
            if action in ("up", "down", "left", "right", "prev", "next"):
                return
        cls = str(call("winfo", "class", f))
        if action in ("up", "down") and cls in ("Listbox", "Treeview"):
            call("event", "generate", f, "<Up>" if action == "up" else "<Down>")
        elif action in ("up", "left", "prev"):
            nxt = str(call("tk_focusPrev", f))
            if nxt:
                call("focus", nxt)
        elif action in ("down", "right", "next"):
            nxt = str(call("tk_focusNext", f))
            if nxt:
                call("focus", nxt)
        elif action == "ok":
            if cls in PRESSABLE:
                call(f, "invoke")
            else:
                call("event", "generate", f, "<Return>")
        elif action == "back":
            close_window(app, top, f)
    except tk.TclError:
        pass                                          # the window went away meanwhile


def first_focusable(app, top: str) -> str:
    call = app.tk.call
    queue = [top]
    while queue:
        w = queue.pop(0)
        try:
            cls = str(call("winfo", "class", w))
            if cls in FOCUSABLE and str(call("winfo", "viewable", w)) == "1":
                try:
                    state = str(call(w, "cget", "-state"))
                except tk.TclError:
                    state = "normal"
                if state != "disabled":
                    return w
            queue.extend(str(c) for c in call("winfo", "children", w))
        except tk.TclError:
            continue
    return ""


def close_window(app, top: str, focused: str = ""):
    """Escape where the window handles it (Tk's question boxes do), else its close button."""
    call = app.tk.call
    if str(call("bind", top, "<Escape>")) or (focused and str(call("bind", focused, "<Escape>"))):
        call("event", "generate", focused or top, "<Escape>")
        return
    cmd = str(call("wm", "protocol", top, "WM_DELETE_WINDOW"))
    if cmd:
        app.tk.eval(cmd)
    else:
        call("destroy", top)


# ---------------------------------------------------------------------------
# The Y menu
# ---------------------------------------------------------------------------
class ActionMenu(tk.Frame):
    """A list of actions drawn over the main window (it stays in the window, so the controller
    keeps working): Up/Down choose, A runs, B or Y closes."""

    def __init__(self, app, items, on_close=None):
        super().__init__(app, background="#1f2a44", padx=2, pady=2, highlightthickness=0)
        self.items = items
        self.on_close = on_close
        inner = tk.Frame(self, background="#f4f6fb", padx=14, pady=10)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text="Actions", font=("TkDefaultFont", 13, "bold"), background="#f4f6fb",
                 anchor="w").pack(fill="x")
        self.lb = tk.Listbox(inner, height=min(len(items), 14), width=34, activestyle="none",
                             font=("TkDefaultFont", 12), exportselection=False, relief="flat",
                             highlightthickness=0, selectbackground="#2f6fd6", selectforeground="#fff")
        for t, _f in items:
            self.lb.insert("end", t)
        self.lb.pack(fill="both", expand=True, pady=(6, 6))
        self.lb.selection_set(0)
        self.lb.bind("<Double-1>", lambda e: self.act("ok"))
        self.lb.bind("<Return>", lambda e: self.act("ok"))
        self.lb.bind("<Escape>", lambda e: self.act("back"))
        tk.Label(inner, text="A: choose   B: close", background="#f4f6fb", foreground="#555",
                 anchor="w").pack(fill="x")
        self.place(relx=0.5, rely=0.5, anchor="center")
        self.lift()
        self.lb.focus_set()

    def index(self):
        sel = self.lb.curselection()
        return sel[0] if sel else 0

    def act(self, action):
        n = len(self.items)
        if action in ("up", "down"):
            i = max(0, min(n - 1, self.index() + (-1 if action == "up" else 1)))
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(i)
            self.lb.see(i)
        elif action == "ok":
            _t, func = self.items[self.index()]
            self.close()
            func()
        elif action in ("back", "menu"):
            self.close()

    def close(self):
        if self.winfo_exists():
            self.destroy()
        if self.on_close:
            cb, self.on_close = self.on_close, None
            cb()
