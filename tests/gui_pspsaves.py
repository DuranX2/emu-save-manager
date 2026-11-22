"""The PSP Save Manager (run with xvfb-run; restarts itself on a 1280x800 screen): PPSSPP's memory
stick with each save's icon, the selected save's background picture behind its details, backing
up, restoring, the already-there question, moving, deleting, and opening it from the main window.
ESM_SHOTS=<dir> saves screenshots."""
import io, os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/pspsavesgui"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
SHOTS = os.environ.get("ESM_SHOTS")
from PIL import Image, ImageDraw
import savefixtures as F
import ui_pspsaves as U

stick, bk = T / "PSP/SAVEDATA", T / "backups"


def png(w, h, a, b, shape=True):
    im = Image.new("RGB", (w, h), a)
    d = ImageDraw.Draw(im)
    for y in range(h):                                     # a simple two-colour gradient
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a[k] * (1 - t) + b[k] * t) for k in range(3)))
    if shape:
        d.ellipse((w * 0.3, h * 0.2, w * 0.7, h * 0.8), outline=(255, 255, 255), width=max(2, w // 60))
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()


def save(folder, title, stitle, detail, cols, pic=True):
    d = stick / folder; d.mkdir(parents=True)
    (d / "PARAM.SFO").write_bytes(F.sfo({"CATEGORY": "MS", "TITLE": title, "SAVEDATA_DIRECTORY": folder,
                                         "SAVEDATA_TITLE": stitle, "SAVEDATA_DETAIL": detail}))
    (d / "ICON0.PNG").write_bytes(png(144, 80, *cols))
    if pic:
        (d / "PIC1.PNG").write_bytes(png(480, 272, cols[1], cols[0], shape=False))
    (d / "DATA.BIN").write_bytes(os.urandom(50000))


save("ULUS10041DATA00", "Daxter", "Slot 1", "Chapter 3: Spider Cave\nPlay time 12:30", ((200, 120, 20), (60, 20, 90)))
save("ULUS10041DATA01", "Daxter", "Slot 2", "Chapter 7", ((200, 120, 20), (60, 20, 90)))
save("ULES00151SAVE", "Space Racer", "Profile: ACE", "Cups won: 6", ((20, 140, 200), (10, 20, 60)))
save("NPUH10091GAME", "Pocket Puzzles", "Auto save", "", ((30, 160, 80), (10, 40, 20)), pic=False)


class FakeApp(tk.Tk):
    backup_dir = bk
    opened = []
    def resolve(self, emu): return {"PPSSPP": {"Saves": stick, "Save States": None}}.get(emu, {})
    def open_folder(self, p): self.opened.append(Path(p))


app = FakeApp(); app.geometry("10x10+0+0")
d = U.PSPSaveDialog(app)
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
    return next(i for i, it in enumerate(v.items[side]) if part in it.title + it.detail)


assert pump(3, lambda: v.running and v.items[0])
assert v.slot[0][0] == "PPSSPP memory stick" and v.slot[1][0] == "Backups"
assert [(it.title, it.detail) for it in v.items[0]] == [("Daxter", "Slot 1"), ("Daxter", "Slot 2"),
                                                        ("Pocket Puzzles", "Auto save"), ("Space Racer", "Profile: ACE")]
assert all(v.canvas.itemcget(f"tile{i}", "image") for i in range(4))
v.move(idx("Slot 1")); pump(0.3)
assert any(k[0] == "psp-panel" for k in v.static)                            # PIC1 behind the panel
print("1. the memory stick's saves with their icons; the selected save's background picture in the panel: ok")
shot("psp_console")

v.act("mark"); v.move(idx("Space")); v.act("mark")
v.act("ok")
assert v.overlay.options[0][1] == "Back up to Backups"
auto_answer("ok", "Close PPSSPP", shot_name="psp_console_question")
pick("copy"); pump(0.5)
assert sorted(p.name for p in (bk / "PSP saves").iterdir()) == ["ULES00151SAVE", "ULUS10041DATA00"]
assert answers == ["Close PPSSPP first"] and "Backed up 2 saves" in v.toast_text
print("2. backing up copies the whole save folders into the backups: ok")

v.move(idx("Slot 1")); v.act("ok")
auto_answer("skip", "Already there")
pick("copy"); pump(0.4)
assert answers[-1] == "Already there" and "Nothing to do" in v.toast_text
print("3. a backup that's already there asks first: ok")

v.act("ok")
auto_answer("yes", "Delete")
pick("delete"); pump(0.4)
assert not (stick / "ULUS10041DATA00").exists() and list((bk / "_psp_safety").rglob("PARAM.SFO"))
v.act("next"); v.move(idx("Slot 1", 1)); v.act("ok")
assert v.overlay.options[0][1] == "Restore to PPSSPP memory stick"
pick("copy"); pump(0.4)
assert (stick / "ULUS10041DATA00/PARAM.SFO").exists() and "Restored 1 save" in v.toast_text
print("4. delete from the memory stick (kept in the safety folder), then restore from the backups: ok")

v.move(idx("Space", 1)); v.act("ok")
auto_answer("replace", "Already there")
pick("move"); pump(0.4)
assert not (bk / "PSP saves/ULES00151SAVE").exists() and (stick / "ULES00151SAVE").exists()
v.act("prev"); v.move(idx("Slot 1")); v.act("ok"); pick("details")
assert "ULUS10041" in v.overlay.message and "America" in v.overlay.message and "Spider Cave" in v.overlay.message
v.act("back")
v.act("menu"); pick("folder")
assert app.opened[-1] == stick
d.destroy(); pump(0.2)
print("5. move, Details (game, region, the save's detail lines) and the folder menu: ok")

import emu_save_manager as esm
esm.App.open_folder = lambda self, p: None
main = esm.App()
main.cfg["overrides"] = {"PPSSPP::Saves": str(stick)}
labels = [main.cards_menu.entrycget(i, "label") for i in range(main.cards_menu.index("end") + 1)
          if main.cards_menu.type(i) != "separator"]
assert "PSP Save Manager…" in labels, labels
main.open_psp_saves(); pump(1)
dlg = [w for w in main.winfo_children() if isinstance(w, U.PSPSaveDialog)]
assert dlg and dlg[0].view.items[0]
main.destroy()
print("6. Memory Cards → PSP Save Manager… opens it on PPSSPP's memory stick: ok")
app.destroy()
print("gui_pspsaves OK")
