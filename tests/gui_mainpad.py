"""Controller navigation in the main window (mainpad.py; run with xvfb-run -a): D-pad moves through
the emulators, saves and backups lists, LB/RB change emulator, A backs up (answering the note
question with the controller too) and restores (answering "Continue?"), B goes back, Y opens the
action menu and A runs its items, and a window with its own controller handling is left alone.
Controller presses are fed to the same handler a real pad uses. ESM_SHOTS=dir saves screenshots."""
import faulthandler, json, os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/mainpadgui"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
SHOTS = os.environ.get("ESM_SHOTS")
faulthandler.dump_traceback_later(150, exit=True)  # a question left open would hang the test
import savefixtures as F

gc = T / "dolphin/GC"
F.gci_file(gc / "USA/Card A/01-GZLE-gczelda2.gci")
F.gci_file(gc / "USA/Card A/8P-GPVE-pikmin.gci", code=b"GPVE", maker=b"8P", fname=b"pikmin",
           title="Pikmin 2", detail="Day 12", icon="ci8shared", banner=False)
(T / "home/.emu_save_manager").mkdir(parents=True)
(T / "home/.emu_save_manager/config.json").write_text(json.dumps({
    "backup_dir": str(T / "backups"), "overrides": {"Dolphin::GameCube Saves": str(gc)}}))
import titledb
titledb.install_roots = lambda extra=(): []
titledb.rom_roots = lambda: []
import emu_save_manager as esm, gamepad, mainpad

app = esm.App()
app.geometry("1120x700+0+0")


def pump(sec=0.3, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())


def shot(name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        app.update(); time.sleep(0.3); app.update()
        subprocess.run(["import", "-window", "root", f"{SHOTS}/{name}.png"], check=False)


def dialog_focus():
    """A window manager gives a new dialog focus; the test's bare X server doesn't, so do it here."""
    g = app.tk.eval("grab current").split(" ")[0]
    if g and str(app.tk.call("winfo", "toplevel", g)) != ".":
        top = str(app.tk.call("winfo", "toplevel", g))
        f = mainpad.first_focusable(app, top) if not mainpad.focus_path(app).startswith(top) else ""
        app.tk.call("focus", "-force", f or top)
        app.update()


def press_in_dialog(action):
    dialog_focus()
    press(action)


pad = app.pad
assert isinstance(pad, mainpad.MainPad), "the main window has controller navigation"


class FakePad:                                   # a plugged-in controller (no real device here)
    name = "test"
    def poll(self): return set()
    def present(self): return True
    def close(self): pass


assert not app.pad_hint.winfo_ismapped(), "no controller: no hints"
pad.nav.backend = FakePad()
pump(2.5, lambda: app.pad_hint.winfo_ismapped())
assert app.pad_hint.winfo_ismapped() and "A: saves" in app.pad_hint.cget("text"), app.pad_hint.cget("text")
app.focus_force(); app.emu_list.focus_set(); pump(0.5)
press = pad.act
names = list(app.profiles)

# 1. D-pad in the emulators list; LB/RB from anywhere
start = app.current
press("down"); pump(0.2)
assert app.current == names[names.index(start) + 1], (start, app.current)
press("up"); pump(0.2)
assert app.current == start
while app.current != "Dolphin":
    press("next"); pump(0.05)
assert app.current == "Dolphin" and app.emu_list.get(app.emu_list.curselection()[0]) == "Dolphin"
pump(1.5)
print("1. emulators: up/down and RB reach", app.current)

# 2. Right / A go to the saves; X selects; B comes back
press("right"); pump(0.2)
assert pad.zone == "saves" and app.focus_get() is app.save_tree and app.save_tree.selection()
press("left"); pump(0.1); assert pad.zone == "emulators"
press("ok"); pump(0.2); assert pad.zone == "saves"
rows = mainpad.visible_rows(app.save_tree)
assert len(rows) >= 2, rows
press("down"); pump(0.1)
assert app.save_tree.focus() == rows[1] and app.save_tree.selection() == (rows[1],)
press("up"); pump(0.1); press("mark"); pump(0.1)
assert set(app.save_tree.selection()) == {rows[0]} and pad.marking, "the first X keeps the save and starts marking"
press("down"); pump(0.1)
assert set(app.save_tree.selection()) == {rows[0]} and app.save_tree.focus() == rows[1], "moving keeps marks"
press("mark"); pump(0.1)
assert set(app.save_tree.selection()) == {rows[0], rows[1]}, "X marks several"
assert "2 selected" in app.status.cget("text"), app.status.cget("text")
press("mark"); pump(0.1)
assert set(app.save_tree.selection()) == {rows[0]}, "X again unmarks"
press("mark"); pump(0.1)
press("back"); pump(0.1); assert pad.zone == "emulators" and app.focus_get() is app.emu_list
print("2. saves: right/A in, down/up, X, B out")
shot("mainpad-saves")

# 3. A in the saves backs them up; the note question is answered with the controller (A = OK)
press("ok"); pump(0.2)
app.save_tree.selection_set(rows[0]); app.save_tree.focus(rows[0])
before = len(app.backup_tree.get_children())
app.after(700, lambda: press_in_dialog("ok"))    # the note window has focus: A presses OK
press("ok")                                      # opens the note question (waits for it)
assert pump(10, lambda: len(app.backup_tree.get_children()) == before + 1), "a backup was made"
print("3. A backed up the selected save; the note question was answered with A")

# 4. backups: A restores, the question answered with B (cancel) and then A (yes)
press("right"); pump(0.2)
assert pad.zone == "backups" and app.backup_tree.selection()
restored = []
orig = esm.execute_restore
esm.execute_restore = lambda *a, **k: restored.append(1) or 1
answers = iter(["back", "ok"])


def answer():
    dialog_focus()
    top = mainpad.focus_window(app)
    if top is not None and top != ".":
        press(next(answers))
    else:
        app.after(200, answer)


app.after(600, answer)
press("ok")                                      # "Continue?" -> B: no
pump(0.5)
assert not restored, "B cancelled the restore"
app.after(600, answer)
app.after(2500, lambda: press_in_dialog("back"))  # then "Restored 1 file(s)": B closes it
press("ok")                                      # "Continue?" -> A: yes
assert pump(6, lambda: restored), "A confirmed the restore"
esm.execute_restore = orig
pump(0.3)
print("4. backups: A restores; the question takes B (cancel) and A (yes)")

# 5. Y opens the action menu; up/down/A run an item; B closes it
app.focus_force(); app.backup_tree.focus_set(); pump(0.2)
press("menu"); pump(0.2)
assert pad.menu is not None and pad.menu.lb.size() >= 10
shot("mainpad-menu")
titles = [t for t, _f in pad.menu.items]
assert titles[0] == "Sync now (both ways)" and "Save History…" in titles and "ROM Library…" in titles
press("back"); pump(0.2)
assert pad.menu is None and pad.zone == "backups"
opened = []
app.open_history = lambda: opened.append("history")
press("menu"); pump(0.1)
for _ in range(titles.index("Save History…")):
    press("down")
press("ok"); pump(0.2)
assert opened == ["history"] and pad.menu is None
print("5. Y menu:", len(titles), "actions; A ran 'Save History…'; B closes")

# 6. a window with its own controller handling is left to it
top = tk.Toplevel(app)
canvas = tk.Canvas(top, width=200, height=100); canvas.pack()
own = []
nav = gamepad.Navigator(canvas, own.append, backend=None, keys=False)
top.focus_force(); canvas.focus_set(); pump(0.3)
assert mainpad.focus_window(app) is None, "the main window's controller handling stays out"
cur = app.current
press("down"); pump(0.1)
assert app.current == cur
nav.stop(); top.destroy(); pump(0.2)
print("6. a console-style window keeps the controller to itself")

# 7. the setting turns it off
app.destroy()
cfgp = T / "home/.emu_save_manager/config.json"
cfg = json.loads(cfgp.read_text()); cfg["controller"] = False; cfgp.write_text(json.dumps(cfg))
app = esm.App(); pump(0.3)
assert app.pad is None
app.destroy()
print("7. \"controller\": false turns it off")
print("GUI MAINPAD OK")
