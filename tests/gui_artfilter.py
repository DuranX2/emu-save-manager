"""ROM Library "Missing cover" / "Missing information" filters, and EmuDeck's Desktop / Cloud /
Emulators folders not shown as systems, the Games column fitting big numbers, and the space
artwork uses (run with xvfb-run -a):
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
# the Games column fits big numbers (libraries over 10k games), and the pane widens with it
app.geometry("1180x720+0+0"); pump(0.5)
w0, sash0 = int(app.sys_tree.column("count", "width")), app.pane.sashpos(0)
ok(w0 == U.LibraryApp.COUNT_W, f"small library: the usual width ({w0})")
app._fit_counts(["26412", "3210 of 26412", "12 of 120"]); pump(0.3)
import tkinter.font as tkfont
font = tkfont.nametofont(U.ttk.Style().lookup("Treeview", "font") or "TkDefaultFont")
w1 = int(app.sys_tree.column("count", "width"))
ok(w1 >= font.measure("3210 of 26412") + 10 and w1 > w0, (w0, w1))
ok(app.pane.sashpos(0) - sash0 == w1 - w0, ("pane widened by the same", sash0, app.pane.sashpos(0)))
app.fill_systems(); pump(0.3)
ok(int(app.sys_tree.column("count", "width")) == w0, "back to normal when the numbers are small again")

# the wheel scrolls the details pane wherever the mouse is on it (not only on the scrollbar)
app.meta.put(key["Bravo"], {"desc": "A long description. " * 200}, replace=True)
app.v_filter.set("All games"); app.fill_games(); pump(0.2)
app.tree.selection_set(key["Bravo"]); app._update_selection(); pump(0.5)
dp = app.details
labels = [w for w in dp.inner.winfo_children() if w.winfo_class() == "TLabel"]
ok(labels and dp.canvas.yview()[1] < 1.0, "a long description that needs scrolling")
y0 = dp.canvas.yview()[0]
if U.SYSTEM == "Windows":
    labels[-1].event_generate("<MouseWheel>", delta=-120)
else:
    labels[-1].event_generate("<Button-5>")
pump(0.2)
ok(dp.canvas.yview()[0] > y0, f"the wheel over a label scrolls the pane ({y0} -> {dp.canvas.yview()[0]})")

# space used by artwork, in the Artwork window
import ui_covers as C
d = C.ArtworkDialog(app); pump(0.2)
ok(pump(20, lambda: "the library:" in d.usage_label.cget("text") and "checking" not in d.usage_label.cget("text")),
   d.usage_label.cget("text"))
text = d.usage_label.cget("text")
ok("this machine" in text and "pictures" in text and "thumbnails" in text and " file" in text, text)
loc = C.M.local_usage(app.meta)
ok(loc["pictures"][1] >= 4 and loc["total"][0] >= loc["pictures"][0], loc)
libsize = C.M.library_usage(app.lib)
ok(libsize and libsize[1] >= 4 and libsize[0] > 0, libsize)
print("   ", text)
d.close(); pump(0.2)
app.destroy()
print(f"gui_artfilter: {checks} checks passed OK")
