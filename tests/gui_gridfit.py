"""ROM Library: when the covers fall a little short of one more column (wide system counts in a big
library), the details pane gives up that width, down to DETAILS_MIN; it never gives more."""
import faulthandler, os, shutil, subprocess, sys, time
from pathlib import Path
if os.environ.get("ESM_GRIDFIT_SCREEN") != "1" and shutil.which("xvfb-run"):
    # a desktop-size screen: on a 1280x800 one the Library uses its compact (Deck) layout
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1400x900x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_GRIDFIT_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/gridfit"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home"); sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(90, exit=True)
import ui_library as U

lib, local = T / "lib", T / "local"
for i in range(12):
    p = lib / "sega32x" / f"Game {i:02d} (USA).32x"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
local.mkdir()
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "covers"})


def pump(t):
    e = time.time() + t
    while time.time() < e:
        app.update(); time.sleep(0.03)


def layout(geom):
    app.geometry(geom + "+0+0"); pump(1.5)
    app._count_extra = 40; app._place_sashes(); pump(0.8)
    return app.grid_view.cols, app.pane.winfo_width() - app.pane.sashpos(1)


cols, details = layout("1178x786")                  # the user's window: was 2 across
assert cols == 3 and app.DETAILS_MIN <= details < app.details_w + 20, (cols, details)
cols, details = layout("1000x700")                  # too far from a third column: details untouched
assert cols == 2 and details > app.DETAILS_MIN + 40, (cols, details)
app.destroy()
print("gui_gridfit OK")
