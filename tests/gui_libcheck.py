"""The ROM Library's Check-up window (run with xvfb-run -a): it opens from the Check-up… button,
lists findings by category, filters by system, explains the selected one, 'Show in library'
selects the entries in the main list, and 'Save report…' writes the text report. Nothing in the
library is changed. ESM_SHOTS=dir saves screenshots."""
import os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_checkup"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import romlibrary as L, ui_library as U, ui_libcheck as C

shots = os.environ.get("ESM_SHOTS")


def mk(path, size):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f: f.truncate(size)
    return p


def pump(w, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        w.update(); time.sleep(0.03)


def wait(w, cond, sec=30):
    end = time.time() + sec
    while time.time() < end:
        pump(w, 0.1)
        if cond():
            pump(w, 0.5)
            return True
    return False


def shot(name):
    if shots and shutil.which("import"):
        os.makedirs(shots, exist_ok=True)
        subprocess.run(["import", "-window", "root", os.path.join(shots, name)], check=False)


GB, MB = 1024 ** 3, 1024 ** 2
lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
mk(lib / "ps2/Space Game (USA).iso", 4 * GB)
mk(lib / "ps2/Space Game (USA).chd", 2 * GB)
mk(lib / "psx/Castle Quest (USA).chd", 500 * MB)
mk(lib / "psx/Castle Quest (USA).png", 40000)
mk(lib / "saturn/Shrine Run (Japan).7z", 300 * MB)
mk(lib / "wiiu/Zoo Party (USA).wux", 3 * GB)
mk(lib / "wiiu/Sky Quest (USA).wua", 5 * GB)
mk(lib / "gc/Plumber Party (USA).rvz", 500 * MB)
local.mkdir(parents=True)
before = sorted((p.relative_to(lib).as_posix(), p.stat().st_size) for p in lib.rglob("*") if p.is_file())

U.messagebox.showinfo = lambda *a, **k: None
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "list"})
app.geometry("1180x720+0+0")
assert wait(app, lambda: app.lib and len(app.lib.items) == 8 and not app._indexing), app.lib and len(app.lib.items)

# 1. the button opens the window; findings by category
d = app.open_checkup()
pump(app, 0.5)
assert isinstance(d, C.CheckupDialog)
cats = [d.tree.item(i, "text") for i in d.tree.get_children()]
assert cats == ["Not games (1)", "Duplicates (1)", "Needs converting (1)", "Wii U: not a .wua yet (1)",
                "Could take less space (1)"], cats
assert d.summary.cget("text").startswith("Checked 8 entries in 5 systems:"), d.summary.cget("text")
print("1. Check-up lists:", cats)
shot("checkup.png")

# 2. selecting a finding explains it; a category explains itself
dup = d.tree.get_children("duplicate")[0]
d.tree.selection_set(dup); pump(app, 0.2)
txt = d.text.get("1.0", "end")
assert "Space Game" in txt and "free 4.0 GB" in txt and "ps2/Space Game (USA).iso" in txt, txt
assert d.show_btn.instate(["!disabled"])
d.tree.selection_set("wiiu"); pump(app, 0.2)
assert ".wua" in d.text.get("1.0", "end")
print("2. details:", txt.splitlines()[0])

# 3. system filter
d.v_sys.set("Wii U"); d.fill(); pump(app, 0.2)
assert d.tree.get_children() == ("wiiu",), d.tree.get_children()
d.v_sys.set(C.ALL); d.fill()
assert "Wii U" in d.sys_box["values"] and "GameCube" not in d.sys_box["values"]
print("3. system filter: ok")

# 4. Show in library selects the entries in the main list (one system shown)
d.tree.selection_set(d.tree.get_children("duplicate")[0]); pump(app, 0.2)
app.v_search.set("castle"); pump(app, 0.4)
d.show_in_library(); pump(app, 0.6)
assert app.v_search.get() == "" and app.system_sel == "ps2", (app.v_search.get(), app.system_sel)
assert set(app.tree.selection()) == {"ps2/Space Game (USA).iso", "ps2/Space Game (USA).chd"}, app.tree.selection()
print("4. Show in library:", app.tree.selection())

# 5. Save report
out = T / "report.txt"
C.filedialog.asksaveasfilename = lambda **k: str(out)
d.save_report()
rep = out.read_text(encoding="utf-8")
assert rep.startswith("ROM Library check-up: 8 entries in 5 systems") and "== Duplicates (1) ==" in rep, rep[:200]
print("5. report saved:", rep.splitlines()[0])

# 6. nothing in the library changed (the app's own .library/machines record is written at start-up)
after = sorted((p.relative_to(lib).as_posix(), p.stat().st_size) for p in lib.rglob("*")
               if p.is_file() and not p.relative_to(lib).as_posix().startswith(".library/"))
assert after == before
d.destroy()
app.destroy()
print("GUI CHECKUP OK")
