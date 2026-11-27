"""Cover grid layout for different case shapes (run with xvfb-run -a):
  * a CD system (square jewel-case covers) gets square tiles, a DVD system tall ones, SNES wide
    ones; a mixed view (all systems) uses DVD-shaped tiles;
  * every cover in a row stands on the same line, and no cover runs into its title;
  * the shape is learned from the covers shown (a few odd ones don't change it) and kept between runs.
ESM_SHOTS=dir saves screenshots."""
import faulthandler, io, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/covershapes"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(170, exit=True)
from PIL import Image, ImageDraw
import romlibrary as L, ui_library as U, ui_covers as C

shots = os.environ.get("ESM_SHOTS")
checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


def png(size, color, label):
    im = Image.new("RGB", size, color)
    ImageDraw.Draw(im).text((10, 10), label, fill="white")
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
shapes = {"psx": [(500, 500)] * 9 + [(500, 700), (520, 480)],      # jewel cases, one long box, one odd scan
          "ps2": [(500, 700)] * 8,
          "snes": [(700, 500)] * 8}
for sysname, sizes in shapes.items():
    for i, _s in enumerate(sizes):
        p = lib / sysname / f"{sysname.upper()} Game {i:02d} (USA).bin"
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(500))
local.mkdir(parents=True)

app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "covers"})
app.geometry("1180x760+0+0")


def pump(sec=0.3, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())


def shot(name):
    if shots:
        os.makedirs(shots, exist_ok=True)
        app.update(); time.sleep(0.3); app.update()
        subprocess.run(["import", "-window", "root", os.path.join(shots, name)], check=False)


assert pump(30, lambda: app.lib and len(app.lib.items) == 27 and not app._indexing)
colors = {"psx": "#3a6ea5", "ps2": "#26364a", "snes": "#7a4ea0"}
for sysname, sizes in shapes.items():
    keys = sorted(k for k, it in app.lib.items.items() if it.system == sysname)
    for k, size in zip(keys, sizes):
        app.meta.put(k, {"title": app.lib.get(k).title}, {"cover": png(size, colors[sysname], f"{size[0]}x{size[1]}")})
pump(0.3)
g = app.grid_view
c = g.canvas


def layout_check(system, shape):
    app.sys_tree.selection_set(system); pump(0.4)
    ok(abs(g.shape - shape) < 0.01, f"{system}: tile shape {g.shape}, expected {shape}")
    ok(g.cover_h == int(round(g.tw * shape)), (g.cover_h, g.tw))
    pump(10, lambda: all(app.images.images.get((k, "cover", g.tw, g.cover_h)) for k in g.keys[:g.cols]))
    g.draw(); pump(0.2)
    imgs = [i for i in c.find_all() if c.type(i) == "image"]
    ok(len(imgs) >= g.cols, f"{system}: covers drawn ({len(imgs)})")
    rows = {}
    for i in imgs:
        x0, y0, x1, y1 = c.bbox(i)
        rows.setdefault(round((y1 - g.GAP) / (g.th + g.GAP)), []).append((y0, y1, x0, x1))
    for r, boxes in rows.items():
        ok(len({b[1] for b in boxes}) == 1, f"{system} row {r}: all covers on one line {sorted({b[1] for b in boxes})}")
        line = boxes[0][1]
        titles = [t for t in c.find_all() if c.type(t) == "text" and c.bbox(t)[1] >= line - 1
                  and c.bbox(t)[1] < line + 20]
        ok(titles, f"{system} row {r}: titles under the covers")
        ok(all(b[1] <= min(c.bbox(t)[1] for t in titles) for b in boxes), "no cover runs into a title")
        for y0, y1, x0, x1 in boxes:
            ok(y0 >= line - g.cover_h - 1 and x1 - x0 <= g.tw + 1, f"{system}: the cover fits its tile")
    full = [b for boxes in rows.values() for b in boxes if b[1] - b[0] >= g.cover_h - 2 or b[3] - b[2] >= g.tw - 2]
    ok(len(full) >= len(imgs) - 2, f"{system}: covers fill their tiles ({len(full)} of {len(imgs)})")


# 1. before anything is learned: the built-in case shapes
ok(app.cover_shapes.shape("psx") == 1.0 and app.cover_shapes.shape("ps2") == 1.4 and app.cover_shapes.shape("snes") == 0.7,
   [app.cover_shapes.shape(s) for s in ("psx", "ps2", "snes")])
layout_check("psx", 1.0)
shot("covershapes_psx.png")
layout_check("ps2", 1.4)
layout_check("snes", 0.7)
shot("covershapes_snes.png")
app.sys_tree.selection_set(U.ALL); pump(0.4)
ok(abs(g.shape - 1.4) < 0.01, "all systems: DVD-shaped tiles")
print("1. CD, DVD and SNES tiles; covers on one line, clear of the titles; mixed view DVD-shaped: ok")

# 2. learned from the covers shown (median: the long box and the odd scan don't change it)
for sysname in shapes:
    app.sys_tree.selection_set(sysname); pump(0.3)
    g.canvas.yview_moveto(1.0); g.draw()
    pump(10, lambda: all(app.images.images.get((k, "cover", g.tw, g.cover_h)) for k in g.keys))
seen = app.cover_shapes.seen
ok(len(seen.get("psx", {})) == 11 and len(seen.get("snes", {})) == 8, {s: len(d) for s, d in seen.items()})
ok(app.cover_shapes.shape("psx") == 1.0, app.cover_shapes.shape("psx"))
ok(app.cover_shapes.shape("snes") == 0.7, app.cover_shapes.shape("snes"))
# a system whose covers turn out tall (US long boxes) learns that
sh = C.CoverShapes(T / "x.json")
for i in range(8):
    sh.note("saturn", f"k{i}", 300, 420)
ok(sh.shape("saturn") == 1.4, sh.shape("saturn"))
sh.note("saturn", "odd", 300, 3000)
ok(sh.shape("saturn") == 1.4, "one odd cover doesn't change it")
ok(C.CoverShapes(T / "x.json").shape("saturn") == 1.0, "not saved yet: built-in")
sh.save()
ok(C.CoverShapes(T / "x.json").shape("saturn") == 1.4, "kept between runs")
(T / "bad.json").write_text("not json")
ok(C.CoverShapes(T / "bad.json").shape("psx") == 1.0, "a broken file is ignored")
app.destroy()
ok((L.APP_DIR / "cover_shapes.json").is_file(), "saved when the Library closes")
print("2. shapes learned from the covers shown, kept between runs: ok")
print(f"gui_covershapes: {checks} checks passed OK")
