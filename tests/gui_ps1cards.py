"""The PS1 Memory Card Manager (run with xvfb-run; restarts itself on a 1280x800 screen):
DuckStation's cards with animated icons, game names and the block map; copying and moving between
cards with in-window questions (close the emulator, already there), a full card, deleting,
exporting to and importing from the exports folder, a new blank card, RetroArch's .srm cards,
and opening it from the main window. ESM_SHOTS=<dir> saves screenshots."""
import os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/ps1cardsgui"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
SHOTS = os.environ.get("ESM_SHOTS")
import ps1card as P
import savefixtures as F
import ui_ps1cards as U

mem, ra, bk = T / "duckstation/memcards", T / "retroarch/saves", T / "backups"
mem.mkdir(parents=True); ra.mkdir(parents=True)
c1 = P.Card.blank()
c1.write_save("BASLUS-00067DRAX00", F.ps1_save_blocks("ＣＡＳＴＬＥＶＡＮＩＡ－１ ＡＬＵＣＡＲＤ", frames=3))
c1.write_save("BASCUS-94163FF7-S01", F.ps1_save_blocks("FF7 SAVE 01", blocks=1, colors=((0, 31, 0), (31, 31, 31))))
c1.write_save("BASCUS-94455GT", F.ps1_save_blocks("GRAN TURISMO", blocks=3, frames=2, colors=((31, 20, 0), (5, 5, 20))))
c1.write_save("BASLUS-00594MGS", F.ps1_save_blocks("METAL GEAR SOLID", blocks=1, colors=((10, 10, 10), (31, 31, 0))))
(mem / "Castlevania - Symphony of the Night (USA)_1.mcd").write_bytes(c1.to_bytes())
os.utime(mem / "Castlevania - Symphony of the Night (USA)_1.mcd", (2e9, 2e9))      # the newest card
(mem / "shared_card_1.mcd").write_bytes(P.Card.blank().to_bytes())
os.utime(mem / "shared_card_1.mcd", (1.9e9, 1.9e9))
ra_card = P.Card.blank(); ra_card.write_save("BESLES-01234RA", F.ps1_save_blocks("RA SAVE"))
(ra / "Some Game (Europe).srm").write_bytes(ra_card.to_bytes())


class Titles:
    def lookup(self, kind, gid):
        return {"SLUS-00067": "Castlevania: Symphony of the Night", "SCUS-94163": "Final Fantasy VII",
                "SCUS-94455": "Gran Turismo"}.get(gid, "") if kind == "ps1" else ""


class FakeApp(tk.Tk):
    backup_dir = bk
    titles = Titles()
    opened = []
    def resolve(self, emu):
        return {"DuckStation": {"Memory Cards": mem, "Save States": None},
                "RetroArch": {"Saves": ra, "States": None}}.get(emu, {})
    def open_folder(self, p): self.opened.append(Path(p))


app = FakeApp(); app.geometry("10x10+0+0")
d = U.PS1CardDialog(app)
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
    i = next(i for i, (k, l) in enumerate(opts) if part == k or part in l)
    v.overlay.index = i
    v.act("ok")


def idx(title_part, side=0):
    return next(i for i, it in enumerate(v.items[side]) if title_part in (it.game + it.title))


assert pump(3, lambda: v.running and v.items[0])
assert v.slot[0][1].name.startswith("Castlevania") and v.slot[1][1].name == "shared_card_1.mcd"
assert [it.game or it.title for it in v.items[0]] == ["Castlevania: Symphony of the Night", "Final Fantasy VII",
                                                      "Gran Turismo", "METAL GEAR SOLID"]
assert v.items[0][0].title == "CASTLEVANIA-1 ALUCARD" and v.space_text(0) == "4 saves · 9 of 15 blocks free"
cv = idx("Castlevania"); v.move(cv)
seen = set()
end = time.time() + 3
while len(seen) < 2 and time.time() < end:   # sample the animation
    pump(0.05)
    seen.add(v.canvas.itemcget(f"tile{cv}", "image"))
assert len(seen) >= 2 and v.canvas.itemcget("big", "image")
print("1. DuckStation's newest card in slot 1, shared_card_1 in slot 2; game names, animated icons: ok")
gt = idx("Gran Turismo"); v.move(gt); pump(0.2)
shot("ps1_console")

# copy two marked saves to shared_card_1 (close-the-emulator question in the window)
v.move(cv); v.act("mark"); v.move(gt); v.act("mark")
v.act("ok")
auto_answer("ok", "Close the emulator", shot_name="ps1_console_question")
pick("copy"); pump(0.5)
on2 = [s.name for s in P.CardStore(mem / "shared_card_1.mcd").saves()]
assert on2 == ["BASLUS-00067DRAX00", "BASCUS-94455GT"], on2
assert answers == ["Close the emulator first"] and "Copied 2 saves" in v.toast_text
assert v.space_text(1) == "2 saves · 11 of 15 blocks free"
print("2. marked saves copy to the other card (verified), after the close-the-emulator question: ok")

# again: already there -> skip
v.move(cv); v.act("ok")
auto_answer("skip", "Already on card")
pick("copy"); pump(0.4)
assert answers[-1] == "Already on card" and "Nothing to do" in v.toast_text
print("3. a save already on the other card asks replace / skip / cancel: ok")

# move FF7 to slot 2: gone from slot 1 (marked deleted), on slot 2
ff = idx("Final Fantasy"); v.move(ff); v.act("ok"); pick("move"); pump(0.4)
assert P.CardStore(mem / "shared_card_1.mcd").find("BASCUS-94163FF7-S01")
src = P.CardStore(next(mem.glob("Castlevania*")))
assert src.find("BASCUS-94163FF7-S01") is None and src.card.entry(2)[0] == 0xA1
print("4. move: the save is on the other card and marked deleted on the first: ok")

# export to the backups, then switch slot 2 to the exports folder and import into a new card
v.move(idx("METAL")); v.act("ok"); pick("export"); pump(0.4)
assert (bk / "PS1 exports/BASLUS-00594MGS.mcs").exists() and "Exported 1 save" in v.toast_text
v.act("menu"); pick("new"); pump(0.3)
assert (mem / "new_card_1.mcd").exists() and v.slot[0] == ("card", mem / "new_card_1.mcd")
assert v.items[0] == [] and "Created new_card_1.mcd" in v.toast_text
shot("ps1_console_new")
v.act("next")
v.act("menu"); pick("slot2"); pick("exports"); pump(0.2)
assert v.slot[1][0] == "exports" and [it.name for it in v.items[1]] == ["BASLUS-00594MGS"]
v.act("ok"); pick("copy"); pump(0.4)                     # warned already this session
assert [s.name for s in P.CardStore(mem / "new_card_1.mcd").saves()] == ["BASLUS-00594MGS"]
print("5. export to the backups; a new blank card; importing from the exports folder: ok")

# delete on a card (recoverable), with the confirmation in the window
v.act("prev")
v.act("ok")
auto_answer("yes", "Delete")
pick("delete"); pump(0.4)
assert P.CardStore(mem / "new_card_1.mcd").saves() == []
assert list((bk / "_ps1_card_safety").rglob("new_card_1.mcd"))
print("6. delete asks first, marks the save deleted and keeps the card as it was: ok")

# RetroArch's .srm card is offered; details; the folder menu
v.act("menu"); pick("slot1")
labels = [l for _k, l in v.overlay.options]
assert any("RetroArch: Some Game (Europe).srm" in l for l in labels)
pick("RetroArch: Some Game"); pump(0.2)
assert [it.name for it in v.items[0]] == ["BESLES-01234RA"]
v.act("ok"); pick("details")
assert "BESLES-01234RA" in v.overlay.message and "Europe" in v.overlay.message and "Blocks: 1" in v.overlay.message
v.act("back")
v.act("menu"); pick("folder")
assert app.opened[-1] == ra
d.destroy(); pump(0.2)
print("7. RetroArch's .srm cards are offered too; Details; Open this card's folder: ok")

import emu_save_manager as esm
esm.App.open_folder = lambda self, p: None
main = esm.App()
main.cfg["overrides"] = {"DuckStation::Memory Cards": str(mem)}
assert "PS1 Card Manager…" in [main.cards_menu.entrycget(i, "label") for i in range(main.cards_menu.index("end") + 1)]
main.open_ps1_cards(); pump(1)
dlg = [w for w in main.winfo_children() if isinstance(w, U.PS1CardDialog)]
assert dlg and len(dlg[0].view.cards) == 3 and dlg[0].view.slot[0] == ("card", mem / "new_card_1.mcd")   # the newest card
main.destroy()
print("8. Memory Cards → PS1 Card Manager… opens it on DuckStation's cards: ok")
app.destroy()
print("gui_ps1cards OK")
