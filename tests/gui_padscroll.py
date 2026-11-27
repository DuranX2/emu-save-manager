"""The right stick, LT and the right-click menus (run with xvfb-run -a):
  * the right stick scrolls the covers, the list and the systems in the ROM Library, and the text
    or list in another window, without moving the selection;
  * LT on a game opens its actions, LT on a system opens the system's menu (look up its artwork,
    leave it out of look-ups, Check-up, BIOS); a right-click shows the same menus;
  * "Use my own cover picture…" saves the picture for every machine, and a later look-up (even
    "look up again") doesn't replace it;
  * systems left out are skipped by "All games" in Artwork…, and can still be looked up on their own.
Presses are fed to the same handler a real pad uses. ESM_SHOTS=dir saves screenshots."""
import faulthandler, io, os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/padscroll"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
faulthandler.dump_traceback_later(170, exit=True)
from PIL import Image
import romlibrary as L, libmeta as M, ui_library as U, ui_covers as C, libpad, mainpad

shots = os.environ.get("ESM_SHOTS")
checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


def mk(path, size=2000):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(size)); return p


def png(color, size=(300, 420)):
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "PNG"); return b.getvalue()


lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
for i in range(60):
    mk(lib / f"gc/Cube Game {i:02d} (USA).rvz")
for i in range(3):
    mk(lib / f"snes/Platformer {i} (USA).sfc")
local.mkdir(parents=True)

app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "covers"})
app.geometry("1180x720+0+0")


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


assert pump(30, lambda: app.lib and len(app.lib.items) == 63 and not app._indexing)
pump(0.5)
pad = app.pad
press = pad.act
app.focus_force(); pump(0.3)

# 1. covers: the right stick scrolls, the selected game stays
app.sys_tree.selection_set("gc"); pump(0.4)
pad.focus("games"); pump(0.3)
g = app.grid_view
sel0 = list(g.sel)
y0 = g.canvas.yview()[0]
for _ in range(3):
    press("scroll_down")
pump(0.2)
ok(g.canvas.yview()[0] > y0, f"covers scrolled down {y0} -> {g.canvas.yview()}")
ok(g.sel == sel0, "the selection didn't move")
ok(g.canvas.find_all(), "the rows scrolled into view are drawn")
press("scroll_up"); press("scroll_up"); press("scroll_up"); pump(0.2)
ok(abs(g.canvas.yview()[0] - y0) < 1e-6, "and back up")
shot("padscroll_covers.png")

# 2. list view and the systems list
app.set_view("list"); pad.focus("games"); pump(0.3)
t = app.tree
sel0, y0 = t.selection(), t.yview()[0]
press("scroll_down"); press("scroll_down"); pump(0.2)
ok(t.yview()[0] > y0 and t.selection() == sel0, "the list scrolls, the selection stays")
pad.focus("systems"); pump(0.2)
press("scroll_down"); pump(0.1)                  # few systems: nothing to scroll, nothing breaks
ok(app.sys_tree.selection() == ("gc",), "scrolling the systems doesn't change the system")
print("1-2. the right stick scrolls the covers, the list and the systems: ok")

# 3. LT on a system: its menu
press("context"); pump(0.2)
ok(pad.menu is not None, "LT on a system opens its menu")
labels = [x for x, _f in pad.menu.items]
ok(labels[:2] == ["Look up artwork for GameCube", "Leave GameCube out of artwork look-ups"], labels)
ok("Check-up…" in labels and "BIOS…" in labels, labels)
pad.menu.lb.selection_clear(0, "end"); pad.menu.lb.selection_set(1)
press("ok"); pump(0.2)
ok(app.scrape_skip() == {"gc"}, app.scrape_skip())
ok(L.load_config().get("artwork", {}).get("skip_systems") == ["gc"], "saved in the settings")
press("context"); pump(0.2)
ok(pad.menu.items[1][0] == "Include GameCube in artwork look-ups again", pad.menu.items[1][0])
press("back"); pump(0.2)
ok(pad.menu is None, "B closes it")

# 4. LT on a game: the game's actions (the same as A)
pad.focus("games"); pump(0.2)
press("context"); pump(0.2)
labels = [x for x, _f in pad.menu.items]
ok("Use my own cover picture…" in labels and "Use my own screenshot…" in labels and "Details…" in labels, labels)
shot("padscroll_lt_menu.png")
press("context"); pump(0.2)
ok(pad.menu is None, "LT again closes it")
print("3-4. LT opens the system's menu and the game's actions: ok")

# 5. right-click menus show the same lists (popup captured, so no menu is left open)
got = []
real_popup = app.popup
app.popup = lambda ev, items, title="": got.append((title, [x for x, _f in items]))


class Ev:
    def __init__(self, x, y): self.x, self.y, self.x_root, self.y_root = x, y, x, y


t.yview_moveto(0); pump(0.2)
row = t.get_children()[4]
bx = t.bbox(row, "title")
app._games_context(Ev(bx[0] + 5, bx[1] + 3)); pump(0.1)
ok(t.selection() == (row,), "right-clicking a game that isn't selected selects it")
ok(got[-1][0] == app.lib.get(row).title and "⬇ Check out" in got[-1][1], got[-1])
t.selection_set(t.get_children()[:3]); pump(0.1)
first = t.get_children()[0]
bx = t.bbox(first, "title")
app._games_context(Ev(bx[0] + 5, bx[1] + 3)); pump(0.1)
ok(len(t.selection()) == 3 and got[-1][0] == "3 games", "right-clicking among the selected keeps them all")
sb = app.sys_tree.bbox("snes")
app._system_context(Ev(sb[0] + 5, sb[1] + 3)); pump(0.4)
ok(app.system_sel == "snes" and got[-1][1][0] == "Look up artwork for SNES", got[-1])
app.sys_tree.selection_set("gc"); pump(0.4)
app.set_view("covers"); pump(0.4)
key = g.keys[2]
hit = next(((x, y) for y in range(5, 400, 10) for x in range(5, 1000, 10) if g._tile_at(x, y) == key), None)
ok(hit is not None, "found the tile")
app._games_context(Ev(*hit)); pump(0.1)
ok(g.sel == [key] and got[-1][0] == app.lib.get(key).title, (g.sel, got[-1]))
app.popup = real_popup
m = app.popup(Ev(10, 10), [("One", lambda: None)], "Title")          # the real menu builds
ok(m.index("end") == 2 and m.entrycget(0, "state") == "disabled", "title, separator, item")
m.unpost(); m.destroy()
print("5. right-click menus for games (list and covers) and systems: ok")

# 6. your own cover: saved for every machine; a later look-up (even "look up again") keeps it
mine = png("red")
U.filedialog.askopenfilename = lambda **kw: str(T / "mine.png")
(T / "mine.png").write_bytes(mine)
app.choose_picture(key, "cover")
ok(pump(20, lambda: app.meta.get(key).get("custom") == ["cover"] and not app.meta._staged_files
        and app.status.cget("text").startswith("New cover saved")), (app.status.cget("text"), app.lib.error))
rel = app.meta.get(key)["media"]["cover"]
cached = (app.meta.cache_dir / rel).read_bytes()
ok((lib / ".library/media" / rel).read_bytes() == cached, "the picture is in the library")
r_, g_, b_ = Image.open(io.BytesIO(cached)).convert("RGB").getpixel((5, 5))
ok(r_ > 240 and g_ < 15 and b_ < 15, "it's the chosen picture (red)")
ok((lib / f".library/meta/gc.json").is_file() and '"custom"' in (lib / ".library/meta/gc.json").read_text(),
   "the game's information file says it's chosen by hand")
U.filedialog.askopenfilename = lambda **kw: str(T / "notapicture.png")
(T / "notapicture.png").write_bytes(b"hello")
shown = []
U.messagebox.showerror = lambda *a, **kw: shown.append(a)
app.choose_picture(key, "cover"); pump(0.2)
ok(shown and "isn't a picture" in shown[-1][1], "a file that isn't a picture is refused")
ok(app.meta.get(key)["media"]["cover"] == rel and (app.meta.cache_dir / rel).read_bytes() == cached, "and changes nothing")


class Source:
    name, label, available = "screenscraper", "test", True
    def lookup(self, item): return {"title": item.title, "desc": "From the source."}, {"cover": png("blue"), "screenshot": png("green")}


U.filedialog.askopenfilename = lambda **kw: str(T / "shot.png")
(T / "shot.png").write_bytes(png("yellow", (640, 480)))
app.choose_picture(key, "screenshot")
ok(pump(20, lambda: app.meta.get(key).get("custom") == ["cover", "screenshot"]
        and app.status.cget("text").startswith("New screenshot saved")), app.status.cget("text"))
srel = app.meta.get(key)["media"]["screenshot"]
scached = (app.meta.cache_dir / srel).read_bytes()
other = app.lib.items[g.keys[5]]
sc = M.Scraper(app.meta, [Source()], [key, g.keys[5]], refresh=True)
sc.run()
ok((app.meta.cache_dir / rel).read_bytes() == cached, "looking up again kept the chosen cover")
ok((app.meta.cache_dir / srel).read_bytes() == scached, "and the chosen screenshot")
ok(app.meta.get(g.keys[5]).get("media", {}).get("screenshot"), "another game gets the source's pictures")
ok(app.meta.get(key).get("desc") == "From the source.", "and the description")
print("6. your own cover is saved to the library and kept by later look-ups: ok")

# 7. Artwork…: "All games" skips the left-out systems; one system on its own is still looked up
scraped = []
app.start_scrape = lambda keys, **kw: scraped.append(list(keys))
d = C.ArtworkDialog(app); pump(0.3)
ok("GameCube" in d.skip_label.cget("text"), d.skip_label.cget("text"))
d.v_scope.set("all"); d.start(); pump(0.1)
ok(scraped[-1] and all(k.startswith("snes/") for k in scraped[-1]), "All games leaves GameCube out")
ok(app.cfg["artwork"].get("skip_systems") == ["gc"], "saving Artwork… keeps the list")
app.scrape_system("gc")
ok(len(scraped[-1]) == 60 and all(k.startswith("gc/") for k in scraped[-1]), "GameCube on its own")
p = C.SystemPicker(d, ["gc", "snes"], {"gc"}); pump(0.2)
p.lb.focus_force(); p.lb.activate(1); pump(0.1)
p.lb.event_generate("<Return>"); pump(0.1)             # A on a controller toggles
p.lb.activate(0); p.lb.event_generate("<Return>"); pump(0.1)
p.ok(); pump(0.1)
ok(p.result == {"snes"}, p.result)
shot("padscroll_artwork.png")
d.close(); pump(0.2)
print("7. left-out systems are skipped by All games, not when looked up on their own: ok")

# 8. another window: the stick scrolls its text (focus on a button) without touching other lists
top = tk.Toplevel(app); top.geometry("400x300+50+50")
fits = tk.Listbox(top, height=5); fits.insert("end", "one", "two"); fits.pack()
txt = tk.Text(top, height=8); txt.insert("end", "\n".join(f"line {i}" for i in range(200))); txt.pack()
btn = tk.Button(top, text="Close"); btn.pack()
pump(0.3)
btn.focus_force(); pump(0.2)
ok(mainpad.scroll_window(app, str(top), 1) and txt.yview()[0] > 0, "the text scrolls while a button has focus")
y = txt.yview()[0]
fits.focus_force(); pump(0.2)
ok(mainpad.scroll_window(app, str(top), 1) and txt.yview()[0] == y, "a focused list that fits doesn't scroll others")
txt.focus_force(); pump(0.2)
mainpad.scroll_window(app, str(top), -1)
ok(txt.yview()[0] < y, "the focused text scrolls back")
top.destroy()
print("8. other windows: the focused (or largest) scrolling area scrolls: ok")

# 9. the console-style views take the stick as the wheel and LT as A
import ui_console
seen = []
fake = type("V", (), {"running": True, "overlay": type("O", (), {"act": lambda s, a: seen.append(a)})()})()
for a in ("scroll_up", "scroll_down", "context", "mark"):
    ui_console.ConsoleBase.act(fake, a)
ok(seen == ["up", "down", "ok", "mark"], seen)
print("9. console-style views: stick moves, LT is A: ok")

app.destroy()
print(f"gui_padscroll: {checks} checks passed OK")
