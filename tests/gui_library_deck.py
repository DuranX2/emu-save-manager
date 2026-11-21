"""ROM Library on a 1280x800 screen (the Steam Deck): the window fills it, rows are touch-sized, a
finger drag scrolls the list, and everything fits.  Run with xvfb-run -a."""
import os, shutil, subprocess, sys, time
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_deck"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import romlibrary as L, ui_library as U

lib, local = T / "library", T / "Emulation/roms"
for i in range(60):
    p = lib / ("ps2" if i % 3 else "gc") / f"Game {i:02d} (USA).iso"; p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f: f.truncate(1024 * 1024 * (i + 1))
local.mkdir(parents=True)
cfg = L.load_config(); cfg.update(library=str(lib), local_roms=str(local), machine_label="Steam Deck", view="list")
app = U.LibraryApp(cfg=cfg)
def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
for _ in range(100):
    pump(0.1)
    if app.lib and len(app.lib.items) == 60 and not app._indexing: break
assert app.compact and (app.winfo_screenwidth(), app.winfo_screenheight()) == (1280, 800)
app.state("normal"); app.geometry("1280x760+0+0"); pump(0.6)       # Deck screen minus Steam's bar
assert int(U.ttk.Style(app).lookup("Lib.Treeview", "rowheight")) == 30
# everything fits: the bottom of the space label is on screen
bottom = app.space_label.winfo_rooty() + app.space_label.winfo_height()
assert bottom <= 760, bottom
vis = app.tree.winfo_height() // 30
print(f"1. fits 1280x760; game list shows about {vis} rows of 30 px")
assert vis >= 8, vis
# finger drag scrolls
app.tree.event_generate("<ButtonPress-1>", x=300, y=200)
for y in range(200, 20, -12):
    app.tree.event_generate("<B1-Motion>", x=300, y=y); pump(0.02)
app.tree.event_generate("<ButtonRelease-1>", x=300, y=20); pump()
assert app.tree.yview()[0] > 0, app.tree.yview()
print("2. dragging a finger scrolls the game list:", f"{app.tree.yview()[0]:.2f}")
shots = os.environ.get("ESM_SHOTS")
if shots and shutil.which("import"):
    app.tree.yview_moveto(0); app.tree.selection_set(app.tree.get_children()[1:3]); pump(0.5)
    subprocess.run(["import", "-window", "root", os.path.join(shots, "library-deck.png")])
app.destroy()
print("ALL OK")
