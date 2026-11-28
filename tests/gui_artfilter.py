"""ROM Library "Missing cover" / "Missing information" filters, and EmuDeck's Desktop / Cloud /
Emulators folders not shown as systems (run with xvfb-run -a):
games without a cover (or without a description) are shown; a game that gets its cover while
the filter is on leaves the list; the controller's Y menu offers the filters too."""
import faulthandler, io, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/artfilter"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(120, exit=True)
from PIL import Image
import ui_library as U, libpad

checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


def png():
    b = io.BytesIO(); Image.new("RGB", (300, 420), "navy").save(b, "PNG"); return b.getvalue()


lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
for n in ("Alpha", "Bravo", "Charlie", "Delta"):
    p = lib / f"snes/{n} (USA).sfc"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(200))
local.mkdir(parents=True)
for d in ("desktop", "cloud", "emulators"):                     # EmuDeck's shortcut folders
    (local / d).mkdir(); (local / d / f"{d} app.sh").write_text("#!/bin/sh\n")
(local / "snes").mkdir(); (local / "snes" / "Desktop").mkdir()  # a real game folder named like one
(local / "snes" / "Desktop" / "Desktop (USA).sfc").write_bytes(os.urandom(100))
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "list"})


def pump(sec=0.3, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())


assert pump(30, lambda: app.lib and len(app.lib.items) == 4 and not app._indexing)
pump(1.0)
systems = set(app.sys_tree.get_children())
ok(not systems & {"desktop", "cloud", "emulators"}, f"EmuDeck's folders aren't systems: {systems}")
ok(any("Desktop" in k for k in app.lib.local), "a game folder called Desktop inside a system still counts")
key = {n: f"snes/{n} (USA).sfc" for n in ("Alpha", "Bravo", "Charlie", "Delta")}
app.meta.put(key["Alpha"], {"title": "Alpha", "desc": "A game."}, {"cover": png()})   # cover + information
app.meta.put(key["Bravo"], {"title": "Bravo", "desc": "B game."})                    # information, no cover
app.meta.put(key["Charlie"], {"title": "Charlie"}, {"cover": png()})                 # cover, no description
pump(0.2)


def shown():                       # the four library games (the local "Desktop" game has no cover either)
    return sorted(k for k in app.tree.get_children() if k in key.values())


app.v_filter.set("Missing cover"); app.fill_games(); pump(0.2)
ok(shown() == sorted([key["Bravo"], key["Delta"]]), shown())
ok(app.count_label.cget("text").startswith("3 games"), app.count_label.cget("text"))
app.v_filter.set("Missing information"); app.fill_games(); pump(0.2)
ok(shown() == sorted([key["Charlie"], key["Delta"]]), shown())

# a cover added while "Missing cover" is on: the game leaves the list
app.v_filter.set("Missing cover"); app.fill_games(); pump(0.2)
(T / "cover.png").write_bytes(png())
U.filedialog.askopenfilename = lambda **kw: str(T / "cover.png")
app.choose_picture(key["Delta"], "cover")
ok(pump(15, lambda: shown() == [key["Bravo"]]), shown())

# the controller's Y menu lists the new filters
labels = [t for t, _f in libpad.library_items(app)]
ok(any("Missing cover" in t for t in labels) and any("Missing information" in t for t in labels), labels)
app.destroy()
print(f"gui_artfilter: {checks} checks passed OK")
