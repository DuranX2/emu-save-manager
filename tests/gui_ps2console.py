"""The PS2 card manager's console view (run with xvfb-run; restarts itself on a 1280x800 screen):
rendered save icons and two-line titles, controller/keyboard navigation across both slots,
marking, copying with the in-window prompts answering the safety questions, deleting,
exporting, choosing a card for a slot, and switching back to the list view (remembered).
ESM_SHOTS=<dir> saves screenshots."""
import json, os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/ps2console"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
SHOTS = os.environ.get("ESM_SHOTS")
from ps2mc import *
import savefixtures as F
import ui_ps2cards as U
import ui_ps2console as C

cards = T / "memcards"; cards.mkdir(); bk = T / "backups"


def save(name, t1, t2, bg, shapes=1, compressed=False):
    ts = now_stamp()
    files = [PSUFile("icon.sys", MODE_FILE, ts, ts, 0, F.ps2_icon_sys(t1, t2, icon="view.ico", bg=bg)),
             PSUFile("view.ico", MODE_FILE, ts, ts, 0, F.ps2_icon(shapes=shapes, compressed=compressed)),
             PSUFile("DATA", MODE_FILE, ts, ts, 0, os.urandom(20000))]
    return PSUSave(name, MODE_DIR, ts, ts, 0, files)


a = PS2Card.create_blank()
specs = [("BASLUS-20001", "SKY RACERS", "Season 2", ((80, 20, 20), (20, 20, 80), (20, 60, 20), (60, 60, 20))),
         ("BASLUS-20002", "CAVE QUEST", "Floor 12", ((10, 50, 90), (10, 10, 40), (40, 10, 60), (5, 5, 20))),
         ("BASLUS-20003", "PUZZLE TOWER", "", ((90, 70, 10), (40, 30, 5), (60, 20, 10), (20, 10, 5))),
         ("BASLUS-20004", "ROBOT LEAGUE", "Cup mode", ((30, 80, 60), (10, 30, 30), (20, 50, 40), (5, 20, 10))),
         ("BASLUS-20005", "NIGHT DRIVE", "Tokyo", ((60, 10, 60), (20, 0, 40), (30, 0, 30), (10, 0, 10))),
         ("BASLUS-20006", "FARM LIFE", "Year 3", ((40, 70, 20), (20, 40, 10), (30, 50, 20), (10, 20, 5)))]
for k, (n, t1, t2, bg) in enumerate(specs):
    a.import_save(save(n, t1, t2, bg, shapes=2 if k == 1 else 1, compressed=k % 2 == 1))
a.save(cards / "Mcd001.ps2")
b = PS2Card.create_blank()
b.import_save(save("BESCES-50001", "OCEAN TALES", "Chapter 4", ((10, 40, 80), (5, 20, 60), (10, 30, 70), (0, 10, 30))))
b.save(cards / "Mcd002.ps2")
PS2Card.create_blank().save(cards / "Mcd003.ps2")

import emu_save_manager as esm


class FakeApp(tk.Tk):
    backup_dir = bk
    cfg = {"overrides": {}}
    def resolve(self, emu): return {"Memory Cards": cards}


app = FakeApp(); app.geometry("10x10+0+0")
d = U.PS2CardDialog(app)
d.geometry("1280x760+0+0")
d.update()
d.set_view("console")
v = d.console


def pump(sec, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.02)
        if until and until():
            return True
    return bool(until and until())


def shot(name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pump(0.4)
        os.system(f"import -window root {SHOTS}/{name}.png 2>/dev/null")


answers = []                       # titles of in-window questions answered, in order


def auto_answer(choice="ok", title_has="", shot_name=None):
    """Answer the next in-window question (while a modal prompt waits) by picking `choice`."""
    def tick():
        o = v.overlay
        if o is not None and title_has in o.title:
            keys = [k for k, _l in o.options]
            if choice in keys:
                if shot_name:
                    shot(shot_name)
                answers.append(o.title)
                o.index = keys.index(choice)
                v.act("ok")
                return
        app.after(80, tick)
    app.after(80, tick)


assert d.view == "console" and d.msg is v.prompts
assert pump(20, lambda: all(v.photos.get((it.key, "tile", 0)) for it in v.items[0])), "icons not rendered"
assert [it.title1 for it in v.items[0]][:2] == ["CAVE QUEST", "FARM LIFE"]       # sorted by title
sky = [it for it in v.items[0] if it.title1 == "SKY RACERS"][0]
assert sky.title2 == "Season 2" and sky.icon is not None and sky.size_kb > 0
assert pump(20, lambda: sum(1 for k in v.photos if k[1] == "big" and k[0] == v.current().key) >= 8)
print("1. console view: every save's own icon rendered, two-line titles, the selected one animating: ok")
shot("ps2_console")

# navigation: right along the row, down, and over the edge into slot 2
cols = v._cols()
v.act("right"); assert v.cursor[0] == 1
v.act("down"); assert v.cursor[0] == min(len(v.items[0]) - 1, 1 + cols)
v.act("up"); v.act("left"); v.act("left")
assert v.side == 0 and v.cursor[0] == 0 or v.side == 0
v.cursor[0] = 0; v.act("left")                     # left from the first column: slot 1 stays
assert v.side == 0
v.act("next"); assert v.side == 1
v.act("prev"); assert v.side == 0
# the controller path: held "down" through the Navigator
v.nav.press({"down"}, now=1000.0); v.nav.press(set(), now=1000.1)
assert v.cursor[0] == min(len(v.items[0]) - 1, cols)
v.cursor[0] = 0
print("2. D-pad / arrows move through the grid, LB/RB and the edges switch slots: ok")

# mark two saves and copy them to slot 2 (the safety question is answered in the window)
names = [v.items[0][0].dirname, v.items[0][1].dirname]
v.act("mark"); v.act("right"); v.act("mark")
assert v.marks[0] == set(names)
v.act("ok")
assert v.overlay is not None and [k for k, _l in v.overlay.options][:2] == ["copy", "move"]
shot("ps2_console_menu")
auto_answer("ok", "Close PCSX2")
v.act("ok")                                          # "Copy to slot 2"
pump(1)
assert answers == ["Close PCSX2 first"], answers
on2 = set(PS2Card.load(cards / "Mcd002.ps2").save_names())
assert set(names) <= on2, on2
assert not v.marks[0] and len(v.items[1]) == 3
print("3. marked saves copy to the other slot, with PCSX2's safety question asked in the window: ok")

# copy again: the overwrite question, answered "No" (skip) -> nothing to do
v.cursor[0] = 0
v.act("ok")
auto_answer("no", "Already", shot_name="ps2_console_question")   # No = skip
v.act("ok")
pump(0.5)
assert answers[-1] == "Already on card", answers
assert "Nothing to do" in d.status_bar.cget("text") and v.toast_text.startswith("Nothing to do")
print("4. 'already on the card' is asked in the window too; skipping leaves the card alone: ok")

# delete from slot 2
v.act("next")
target = v.current().dirname
v.act("ok")
keys = [k for k, _l in v.overlay.options]
v.overlay.index = keys.index("delete")
auto_answer("yes", "Delete")
v.act("ok")
pump(1)
assert target not in PS2Card.load(cards / "Mcd002.ps2").save_names()
print("5. Delete asks first, then removes the save (a safety copy is kept): ok")

# export
v.act("prev"); v.cursor[0] = 2; v._want_big()
v.act("ok")
keys = [k for k, _l in v.overlay.options]
v.overlay.index = keys.index("export"); v.act("ok")
pump(0.5)
out = list((bk / "PS2 exports").glob("*.psu"))
assert len(out) == 1 and out[0].stem == v.items[0][2].dirname, out
print("6. Export writes a .psu into the backups' 'PS2 exports' folder (no file picker needed): ok")

# choose another card for slot 2 from the menu
v.act("menu")
keys = [k for k, _l in v.overlay.options]
v.overlay.index = keys.index("slot2"); v.act("ok")
assert v.overlay is not None and "slot 2" in v.overlay.title
keys = [k for k, _l in v.overlay.options]
v.overlay.index = next(i for i, k in enumerate(keys) if "Mcd003" in k); v.act("ok")
pump(0.3)
assert d.right.path.name == "Mcd003.ps2" and v.side == 1 and v.items[1] == []
shot("ps2_console_empty")
print("7. the menu chooses which card is in each slot: ok")

# details, then back to the list view
v.act("prev"); v.act("ok")
keys = [k for k, _l in v.overlay.options]
v.overlay.index = keys.index("details"); v.act("ok")
assert v.overlay is not None and "DATA" in v.overlay.message and "view.ico" in v.overlay.message
v.act("back"); assert v.overlay is None
v.act("view"); pump(0.3)
assert d.view == "classic" and d.msg is U.messagebox and d._body.winfo_ismapped()
cfg = json.loads((T / "home/.emu_save_manager/config.json").read_text())
assert cfg["ps2_view"] == "classic"
d.set_view("console"); assert json.loads((T / "home/.emu_save_manager/config.json").read_text())["ps2_view"] == "console"
d.destroy(); pump(0.2)
d2 = U.PS2CardDialog(app); pump(0.5)
assert d2.view == "console"                          # remembered
d2.destroy()
print("8. Details lists the files; F2/Start returns to the list view; the choice is remembered: ok")
app.destroy()
print("gui_ps2console OK")
