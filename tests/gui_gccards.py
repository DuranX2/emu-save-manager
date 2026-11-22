"""The GameCube Memory Card Manager (run with xvfb-run; restarts itself on a 1280x800 screen):
Dolphin's Card A with animated icons and banners, the exports folder as slot 2, copying and
moving with in-window questions (close Dolphin, already there, different region), the game's
"no copy" flag, delete, export, choosing slots, and opening it from the main window.
ESM_SHOTS=<dir> saves screenshots."""
import json, os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/gccardsgui"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
SHOTS = os.environ.get("ESM_SHOTS")
import savefixtures as F
import ui_gccards as U

gc = T / "GC"
a = gc / "USA/Card A"
F.gci_file(a / "01-GZLE-gczelda2.gci", mtime=0x2B000000)
F.gci_file(a / "01-GZLE-gczelda1.gci", fname=b"gczelda1", detail="File 1")
F.gci_file(a / "8P-GPVE-pikmin.gci", code=b"GPVE", maker=b"8P", fname=b"pikmin", title="Pikmin 2",
           detail="Day 12", icon="anim", bounce=True)
F.gci_file(a / "01-GALE-smash.gci", code=b"GALE", fname=b"SSB", title="Smash Bros", detail="Records", perms=0x0C)
F.gci_file(a / "01-GM4E-mkdd.gci", code=b"GM4E", fname=b"MKDD", title="Mario Kart", detail="Ghost data",
           icon="ci8shared", banner=False)
F.gci_file(a / "01-GPOP-japan.gci", code=b"GPOJ", fname=b"jp", title="Japanese Game", detail="Slot 1")
bk = T / "backups"


class FakeApp(tk.Tk):
    backup_dir = bk
    opened = []
    def resolve(self, emu): return {"GameCube Saves": gc, "Wii Saves": None, "Save States": None}
    def open_folder(self, p): self.opened.append(Path(p))


app = FakeApp(); app.geometry("10x10+0+0")
d = U.GCCardDialog(app)
v = d.view


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
        pump(0.3)
        os.system(f"import -window root {SHOTS}/{name}.png 2>/dev/null")


answers = []


def auto_answer(choice, title_has="", shot_name=None, times=1):
    left = [times]

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
                left[0] -= 1
                if left[0] <= 0:
                    return
        app.after(60, tick)
    app.after(60, tick)


def pick(key_part):
    keys = [k for k, _l in v.overlay.options]
    labels = [l for _k, l in v.overlay.options]
    i = next(i for i, (k, l) in enumerate(zip(keys, labels)) if key_part == k or key_part in l)
    v.overlay.index = i
    v.act("ok")


assert pump(3, lambda: v.running and v.items[0])
assert v.slot[0].label == "Card A (USA)" and v.slot[1].label == "Exported saves"
titles = [it.title for it in v.items[0]]
assert titles == ["Japanese Game", "Mario Kart", "Pikmin 2", "Smash Bros", "The Legend of Zelda",
                  "The Legend of Zelda"], titles
pik = next(i for i, it in enumerate(v.items[0]) if it.title == "Pikmin 2")
assert v.items[0][pik].ticks == [0, 1, 1, 2, 2, 2, 1, 1]               # back and forth
v.move(pik); pump(0.3)
seen = set()
end = time.time() + 3
while len(seen) < 3 and time.time() < end:   # sample the animation
    pump(0.05)
    seen.add(v.canvas.itemcget(f"tile{pik}", "image"))
assert len(seen) >= 3, seen                                            # the tile animates
assert v.canvas.itemcget("big", "image")
print("1. Card A with every save's animated icon (Pikmin's plays back and forth) and slot 2 = exports: ok")
z2 = next(i for i, it in enumerate(v.items[0]) if it.detail == "File 2")
v.move(z2); pump(0.2)
shot("gc_console")

# export (no questions), then the exports slot shows it
v.act("ok"); pick("export"); pump(0.3)
assert (bk / "GameCube exports/01-GZLE-gczelda2.gci").exists()
assert len(v.items[1]) == 1 and "Exported 1 save" in v.toast_text
print("2. Export copies the save into the backups' 'GameCube exports' folder (slot 2): ok")

# choose Card B (not set up yet) for slot 2, copy two marked saves there
v.act("menu"); pick("slot2")
assert v.overlay is not None and "not set up yet" in " ".join(l for _k, l in v.overlay.options)
pick("Card B (USA)"); pump(0.2)
assert v.slot[1].label == "Card B (USA)" and v.side == 1 and v.items[1] == []
shot("gc_console_empty")
v.act("prev")
m1 = next(i for i, it in enumerate(v.items[0]) if it.title == "Mario Kart")
v.move(m1); v.act("mark"); v.move(pik); v.act("mark")
v.act("ok")
auto_answer("ok", "Close Dolphin", shot_name="gc_console_question")
pick("copy")
pump(0.5)
assert sorted(p.name for p in (gc / "USA/Card B").glob("*.gci")) == ["01-GM4E-mkdd.gci", "8P-GPVE-pikmin.gci"]
assert answers == ["Close Dolphin first"] and "Copied 2 saves to Card B (USA)" in v.toast_text
print("3. marked saves copy to Card B (created), after the 'close Dolphin' question in the window: ok")

# copy one again: already there -> skip; the warning isn't repeated
v.move(pik)
v.act("ok")
auto_answer("skip", "Already on card")
pick("copy"); pump(0.4)
assert answers[-1] == "Already on card" and answers.count("Close Dolphin first") == 1
assert "Nothing to do" in v.toast_text
print("4. a save already on the other card asks replace / skip / cancel: ok")

# no-copy save: refused with an explanation
sm = next(i for i, it in enumerate(v.items[0]) if it.title == "Smash Bros")
v.move(sm); v.act("ok")
auto_answer("ok", "weren't changed")
pick("copy"); pump(0.4)
assert answers[-1] == "Some saves weren't changed" and not (gc / "USA/Card B/01-GALE-smash.gci").exists()
print("5. a save the game marked \"no copy\" isn't copied, and the window says why: ok")

# Japanese save to a USA card: asks about the region
v.act("menu"); pick("slot2"); pick("Card A (JAP)") if any("JAP" in l for _k, l in v.overlay.options) else None
if v.overlay is not None:
    v.act("back")
(gc / "EUR/Card A").mkdir(parents=True)
v.reload()
v.act("menu"); pick("slot2"); pick("Card A (EUR)"); pump(0.2)
v.act("prev")
jp = next(i for i, it in enumerate(v.items[0]) if it.title == "Japanese Game")
v.move(jp); v.act("ok")
auto_answer("no", "Different region")
pick("copy"); pump(0.4)
assert answers[-1] == "Different region" and not list((gc / "EUR/Card A").glob("*.gci"))
print("6. copying to another region's card asks first (Dolphin wouldn't show it there): ok")

# move Zelda File 1 to EUR (answer yes), then delete it there
z1 = next(i for i, it in enumerate(v.items[0]) if it.detail == "File 1")
v.move(z1); v.act("ok")
auto_answer("yes", "Different region")
pick("move"); pump(0.4)
assert (gc / "EUR/Card A/01-GZLE-gczelda1.gci").exists() and not (a / "01-GZLE-gczelda1.gci").exists()
v.act("next")
v.act("ok")
auto_answer("yes", "Delete")
pick("delete")
pump(0.5)
assert not list((gc / "EUR/Card A").glob("*.gci"))
assert len(list((bk / "_gc_card_safety").rglob("01-GZLE-gczelda1.gci"))) == 2     # moved away + deleted
print("7. move then delete: both keep safety copies: ok")

# details and the folder button
v.act("prev"); v.move(pik)
v.act("ok"); pick("details")
assert "3 frames, plays back and forth" in v.overlay.message and "GPVE8P" in v.overlay.message
v.act("back")
v.act("menu"); pick("folder")
assert app.opened and app.opened[-1] == a
d.destroy(); pump(0.2)
print("8. Details describes the save; the menu opens its folder: ok")

# from the main window
import emu_save_manager as esm
esm.App.open_folder = lambda self, p: None
main = esm.App()
main.cfg["overrides"] = {"Dolphin::GameCube Saves": str(gc)}
main.open_gc_cards(); pump(1)
dlg = [w for w in main.winfo_children() if isinstance(w, U.GCCardDialog)]
assert dlg and dlg[0].gc_root == gc and dlg[0].view.items[0]
main.destroy()
print("9. 'GameCube Card Manager…' in the main window opens it on Dolphin's folder: ok")
app.destroy()
print("gui_gccards OK")
