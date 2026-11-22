"""The Wii Save Manager (run with xvfb-run; restarts itself on a 1280x800 screen): Dolphin's
system memory with animated icons and banners, backing up to the backups ("SD card"), restoring,
the already-there question, moving, deleting, choosing PrimeHack's system memory, and opening it
from the main window. ESM_SHOTS=<dir> saves screenshots."""
import os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/wiisavesgui"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
SHOTS = os.environ.get("ESM_SHOTS")
import savefixtures as F
import ui_wiisaves as U

wii, ph, bk = T / "dolphin/Wii", T / "primehack/Wii", T / "backups"
def game(root, lo, title, sub, icons=1, flags=0, size=4000):
    d = root / "title/00010000" / lo / "data"
    F.wii_banner(d / "banner.bin", title, sub, icons=icons, flags=flags)
    (d / "save.bin").write_bytes(os.urandom(size))
game(wii, "524d4745", "Super Mario Galaxy", "Mario: 3 stars", icons=3, flags=0x10, size=300000)
game(wii, "52534245", "Super Smash Bros. Brawl", "Records", flags=0x01)
game(wii, "524d4345", "Mario Kart Wii", "License 1", icons=2)
nob = wii / "title/00010000/525a4445/data"; nob.mkdir(parents=True); (nob / "x.bin").write_bytes(b"x")
(wii / "title/00000001/00000002/data").mkdir(parents=True); (wii / "title/00000001/00000002/data/s").write_bytes(b"s")
game(ph, "52334d45", "Metroid Prime Trilogy", "Slot A")


class Titles:
    def lookup(self, kind, gid): return {"RZDE": "Zelda: Twilight Princess"}.get(gid, "") if kind == "wii" else ""


class FakeApp(tk.Tk):
    backup_dir = bk
    titles = Titles()
    opened = []
    def resolve(self, emu):
        return {"Dolphin": {"Wii Saves": wii}, "PrimeHack": {"Wii Saves": ph}}.get(emu, {})
    def open_folder(self, p): self.opened.append(Path(p))


app = FakeApp(); app.geometry("10x10+0+0")
d = U.WiiSaveDialog(app)
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


def auto_answer(choice, title_has="", shot_name=None):
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
        app.after(60, tick)
    app.after(60, tick)


def pick(part):
    opts = v.overlay.options
    v.overlay.index = next(i for i, (k, l) in enumerate(opts) if part == k or part in l)
    v.act("ok")


def idx(part, side=0):
    return next(i for i, it in enumerate(v.items[side]) if part in it.title)


assert pump(3, lambda: v.running and v.items[0])
assert v.slot[0][0] == "Dolphin system memory" and v.slot[1][0] == "Backups"
assert [it.title for it in v.items[0]] == ["Mario Kart Wii", "Super Mario Galaxy", "Super Smash Bros. Brawl",
                                           "Zelda: Twilight Princess"]
assert v.space_text(0) == "4 saves · 6 blocks"
g = idx("Galaxy"); v.move(g)
seen = set()
end = time.time() + 3
while len(seen) < 3 and time.time() < end:   # sample the animation
    pump(0.05)
    seen.add(v.canvas.itemcget(f"tile{g}", "image"))
assert len(seen) >= 3 and v.canvas.itemcget("big", "image")
print("1. Dolphin's game saves (system software hidden) with animated icons; names from banners or the lookup: ok")
shot("wii_console")

# back up two saves
v.act("mark"); v.move(idx("Kart")); v.act("mark")
v.act("ok")
assert v.overlay.options[0][1] == "Back up to Backups"
auto_answer("ok", "Close Dolphin", shot_name="wii_console_question")
pick("copy"); pump(0.5)
assert sorted(p.name for p in (bk / "Wii saves").iterdir()) == ["00010000-524D4345", "00010000-524D4745"]
assert answers == ["Close Dolphin first"] and "Backed up 2 saves" in v.toast_text and len(v.items[1]) == 2
print("2. backing up copies whole save folders into the backups: ok")

# again -> already there, replace
v.move(idx("Galaxy")); v.act("ok")
auto_answer("replace", "Already there")
pick("copy"); pump(0.5)
assert answers[-1] == "Already there" and list((bk / "_wii_safety").rglob("save.bin"))
print("3. a backup that's already there asks first; replacing keeps the old one: ok")

# delete Galaxy from the system memory, then restore it from the backups
v.move(idx("Galaxy")); v.act("ok")
auto_answer("yes", "Delete")
pick("delete"); pump(0.4)
assert not (wii / "title/00010000/524d4745/data").exists() and "Galaxy" not in " ".join(it.title for it in v.items[0])
v.act("next"); v.move(idx("Galaxy", 1))
v.act("ok")
assert v.overlay.options[0][1] == "Restore to Dolphin system memory"
pick("copy"); pump(0.5)
assert (wii / "title/00010000/524d4745/data/banner.bin").exists() and "Restored 1 save" in v.toast_text
print("4. delete from the system memory (safety copy kept), then restore it from the backups: ok")

# move Mario Kart's backup into PrimeHack's memory (slot 1 -> PrimeHack)
v.act("menu"); pick("slot1")
assert any("PrimeHack system memory" in l for _k, l in v.overlay.options)
pick("PrimeHack system memory"); pump(0.2)
assert [it.title for it in v.items[0]] == ["Metroid Prime Trilogy"]
v.act("next"); v.move(idx("Kart", 1)); v.act("ok"); pick("move"); pump(0.5)
assert (ph / "title/00010000/524d4345/data/save.bin").exists() and not (bk / "Wii saves/00010000-524D4345").exists()
print("5. PrimeHack's system memory can be chosen; move takes a backup out of the backups: ok")

# details mention the no-copy flag
v.act("menu"); pick("slot1"); pick("Dolphin system memory"); pump(0.2)
v.move(idx("Smash")); v.act("ok"); pick("details")
assert "no copy" in v.overlay.message and "52534245" in v.overlay.message
v.act("back")
v.act("menu"); pick("folder")
assert app.opened[-1] == wii / "title"
d.destroy(); pump(0.2)
print("6. Details explains the game's \"no copy\" flag; the menu opens the folder: ok")

import emu_save_manager as esm
esm.App.open_folder = lambda self, p: None
main = esm.App()
main.cfg["overrides"] = {"Dolphin::Wii Saves": str(wii)}
labels = [main.cards_menu.entrycget(i, "label") for i in range(main.cards_menu.index("end") + 1)]
assert labels[:4] == ["PS2 Card Manager…", "PS1 Card Manager…", "GameCube Card Manager…", "Wii Save Manager…"], labels
main.open_wii_saves(); pump(1)
dlg = [w for w in main.winfo_children() if isinstance(w, U.WiiSaveDialog)]
assert dlg and dlg[0].view.items[0]
main.destroy()
print("7. Memory Cards → Wii Save Manager… opens it on Dolphin's Wii folder: ok")
app.destroy()
print("gui_wiisaves OK")
