"""Controller navigation in the ROM Library window (libpad.py; run with xvfb-run -a): covers grid
moves, Left at a row's start goes to the systems, LB/RB change system, A opens a game's actions and
checks it out, the "Check in?" question is answered with the controller, X selects several games,
Y opens the Library menu (filters, view), View switches covers/list, and the list view works too.
Presses are fed to the same handler a real pad uses. ESM_SHOTS=dir saves screenshots."""
import faulthandler, json, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_pad"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(150, exit=True)
import romlibrary as L, ui_library as U, libpad, mainpad

shots = os.environ.get("ESM_SHOTS")


def mk(path, size=2000):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(size)); return p


lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
for i in range(9):
    mk(lib / f"gc/Cube Game {i} (USA).rvz")
for i in range(3):
    mk(lib / f"ps2/Space Game {i} (USA).chd")
mk(lib / "snes/Platformer (USA).sfc")
local.mkdir(parents=True)

app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "covers"})
app.geometry("1180x720+0+0")


def pump(sec=0.3, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())


def shot(name):
    if shots:
        os.makedirs(shots, exist_ok=True)
        app.update(); time.sleep(0.3); app.update()
        subprocess.run(["import", "-window", "root", os.path.join(shots, name)], check=False)


def dialog_focus():
    g = app.tk.eval("grab current").split(" ")[0]
    if g and str(app.tk.call("winfo", "toplevel", g)) != ".":
        top = str(app.tk.call("winfo", "toplevel", g))
        f = mainpad.first_focusable(app, top) if not mainpad.focus_path(app).startswith(top) else ""
        app.tk.call("focus", "-force", f or top)
        app.update()


assert pump(30, lambda: app.lib and len(app.lib.items) == 13 and not app._indexing)
pump(0.5)
pad = app.pad
assert isinstance(pad, libpad.LibraryPad)
press = pad.act
app.focus_force(); pump(0.3)


class FakePad:
    name = "test"
    def poll(self): return set()
    def present(self): return True
    def close(self): pass


pad.nav.backend = FakePad()
assert pump(3, lambda: app.pad_hint.winfo_ismapped()) and "Y menu" in app.pad_hint.cget("text")

# 1. LB/RB pick a system; the cover grid moves with the D-pad; Left at a row's start -> systems
app.sys_tree.selection_set(U.ALL); pump(0.3)
press("next"); pump(0.4)
assert app.system_sel == "gc", app.system_sel
pad.focus("games"); pump(0.2)
g = app.grid_view
cols = g.cols
assert cols >= 2 and g.sel == [g.keys[0]], (cols, g.sel)
press("right"); pump(0.1); assert g.sel == [g.keys[1]]
press("down"); pump(0.1); assert g.sel == [g.keys[1 + cols]], (g.sel, cols)
press("up"); press("left"); pump(0.1); assert g.sel == [g.keys[0]]
press("left"); pump(0.1); assert pad.zone == "systems" and app.focus_get() is app.sys_tree
press("down"); pump(0.4); assert app.system_sel == "ps2", app.system_sel
press("ok"); pump(0.2); assert pad.zone == "games" and g.sel == [g.keys[0]] and g.keys[0].startswith("ps2/")
press("prev"); pump(0.5); assert app.system_sel == "gc" and pad.zone == "games"
print(f"1. covers: {cols} per row; D-pad, Left to systems, LB/RB between systems: ok")
shot("libpad-covers.png")

# 2. A opens the game's actions; Check out runs
first = g.keys[0]
press("ok"); pump(0.2)
assert pad.menu is not None and pad.menu.items[0][0] == "⬇ Check out", [t for t, _ in pad.menu.items]
shot("libpad-menu.png")
press("ok")
assert pump(20, lambda: app.lib.status(first)[0] == "here"), app.lib.status(first)
pump(0.8)
print("2. A → actions → Check out:", app.lib.status(first)[0])

# 3. Check in asks first: B cancels, A confirms (Tk's own question box)
app.focus_force(); g.canvas.focus_set(); pump(0.2)
g.set_selection([first]); pump(0.1)
press("ok"); pump(0.2)
titles = [t for t, _ in pad.menu.items]
assert "⬆ Check in" in titles, titles
for _ in range(titles.index("⬆ Check in")):
    press("down")
answers = iter(["back"])
def answer():
    dialog_focus()
    top = mainpad.focus_window(app)
    if top not in (None, "."):
        press(next(answers))
    else:
        app.after(200, answer)
app.after(500, answer)
press("ok"); pump(1.0)
assert app.lib.status(first)[0] == "here", "B kept it"
app.focus_force(); g.canvas.focus_set(); pump(0.2)
press("ok"); pump(0.2)
for _ in range(titles.index("⬆ Check in")):
    press("down")
answers = iter(["ok"])
app.after(500, answer)
press("ok")
assert pump(20, lambda: app.lib.status(first)[0] == "not_here"), app.lib.status(first)
print("3. Check in: B cancelled, A confirmed")

# 4. X selects several games; A shows actions for all of them
app.focus_force(); g.canvas.focus_set(); pump(0.3)
g.set_selection([g.keys[0]]); pump(0.1)
press("mark"); press("right"); press("mark"); press("right"); press("mark"); pump(0.2)
assert len(app.selected()) == 3 and pad.marking, app.selected()
press("down"); pump(0.1)
assert len(app.selected()) == 3, "moving keeps the marks"
press("ok"); pump(0.2)
assert pad.menu is not None and pad.menu.lb.master.winfo_children()[0].cget("text") == "3 games"
fav = [i for i, (t, _) in enumerate(pad.menu.items) if "Favourite" in t][0]
for _ in range(fav):
    press("down")
press("ok")
assert pump(5, lambda: all(app.lib.is_favourite(k) for k in g.keys[:3])), "favourited all three"
print("4. X marked 3 games; A → Favourite for all of them")

# 5. Y: the Library menu; filter and view; View button; the list view
app.focus_force(); pump(0.2)
press("menu"); pump(0.2)
titles = [t for t, _ in pad.menu.items]
assert titles[:5] == ["Refresh", "Check-up…", "BIOS…", "Artwork…", "Settings…"], titles
fav_f = [i for i, t in enumerate(titles) if t.endswith("Show: Favourites")][0]
for _ in range(fav_f):
    press("down")
press("ok"); pump(0.4)
assert app.v_filter.get() == "Favourites" and len(g.keys) == 3, (app.v_filter.get(), len(g.keys))
press("menu"); pump(0.1); press("back"); pump(0.1); assert pad.menu is None
app.v_filter.set(U.FILTERS[0]); app.fill_games(); pump(0.3)
press("view"); pump(0.4)
assert app.view == "list" and pad.zone == "games"
rows = app.tree.get_children()
press("down"); pump(0.1)
assert app.tree.selection() == (rows[1],), (app.tree.selection(), rows[:2])
press("left"); pump(0.1); assert pad.zone == "systems"
press("right"); pump(0.1); assert pad.zone == "games"
press("view"); pump(0.3); assert app.view == "covers"
print("5. Y menu (filter), B closes, View switches covers/list, list view moves: ok")

app.destroy()
print("GUI LIBPAD OK")
