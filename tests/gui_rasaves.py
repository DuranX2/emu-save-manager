"""The RetroArch Save Manager (run with xvfb-run; restarts itself on a 1280x800 screen): games with box
art (RetroArch's own thumbnails and a stubbed download), state screenshots cycling in the panel,
the systems screen and per-system tabs, one-game backup / restore / delete, renaming saves whose ROM was renamed, tidying
scattered saves, the DuckStation card and .sav conversions, scrolling menus, and opening it from the
main window. ESM_SHOTS=<dir> saves screenshots."""
import io, json, os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/rasavesgui"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
SHOTS = os.environ.get("ESM_SHOTS")
from PIL import Image, ImageDraw
import ps1card, savefixtures as F
import rasaves as R
import ui_rasaves as U

ra, bk, roms = T / "retroarch", T / "backups", T / "Emulation/roms"
saves, states = ra / "saves", ra / "states"
for d in (saves, states, ra / "playlists", roms / "gba", roms / "snes", roms / "psx", T / "duck"):
    d.mkdir(parents=True, exist_ok=True)
(ra / "retroarch.cfg").write_text('savefile_directory = ":/saves"\nsavestate_directory = ":/states"\n'
                                  'sort_savefiles_enable = "false"\nsort_savestates_enable = "false"\n')


def png(w, h, a, b, label=""):
    im = Image.new("RGB", (w, h), a)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a[k] * (1 - t) + b[k] * t) for k in range(3)))
    d.rectangle((w * 0.15, h * 0.15, w * 0.85, h * 0.85), outline=(255, 255, 255), width=max(2, w // 50))
    if label:
        d.text((w * 0.2, h * 0.4), label, fill=(255, 255, 255))
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()


def put(p, data=b"x", age=0):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    t = time.time() - age; os.utime(p, (t, t)); return p


for n in ("Metroid Fusion (USA).gba", "Golden Sun (USA).gba", "Pokemon - Emerald Version (USA, Europe).gba"):
    put(roms / "gba" / n, b"rom")
put(roms / "snes" / "Super Metroid (USA).sfc", b"rom")
put(roms / "psx" / "Spyro the Dragon (USA).chd", b"rom")
art = ra / "thumbnails/Nintendo - Game Boy Advance/Named_Boxarts"
put(art / "Metroid Fusion (USA).png", png(256, 360, (200, 60, 30), (40, 10, 60), "MF"))
put(saves / "Metroid Fusion (USA).srm", b"MF" * 4000, age=600)
for slot, suffix, cols in ((1, ".state1", ((30, 90, 200), (5, 10, 40))), (2, ".state2", ((30, 170, 80), (5, 40, 10))),
                           (-1, ".state.auto", ((200, 160, 40), (60, 30, 5)))):
    put(states / f"Metroid Fusion (USA){suffix}", b"S" * 3000, age=100 + slot)
    put(states / f"Metroid Fusion (USA){suffix}.png", png(240, 160, *cols, suffix))
put(saves / "Golden Sun (USA).srm", b"GS" * 1000)
put(saves / "Super Metroid (USA).srm", b"SM" * 1000)
put(saves / "Pokemon Emerald (U).srm", b"PE")                         # an orphan
put(saves / "mGBA" / "Golden Sun (USA).srm", b"GS-newer", age=-5)      # scattered, and newer
card = ps1card.Card.blank(); card.write_save("BASCUS-94228SPYRO", F.ps1_save_blocks("Spyro the Dragon", 1))
put(saves / "Spyro the Dragon (USA).srm", bytes(card.img))
put(T / "duck" / "Spyro the Dragon_1.mcd", bytes(ps1card.Card.blank().img))
for i in range(30):                                                    # games of no known system
    put(saves / f"Extra Game {i:02d}.srm", b"e")
MANY = ["nes", "genesis", "n64", "nds", "gb", "gbc", "mastersystem", "gamegear", "pcengine", "saturn",
        "atari2600", "atarilynx", "ngp", "wonderswan"]
for k in MANY:                                                          # enough systems to overflow the tabs
    put(roms / k / f"Sample {k} Game.bin", b"rom")
    put(saves / f"Sample {k} Game.srm", b"s")

got = []


class Http404(Exception):
    code = 404


def fake_get(url):
    got.append(url)
    if "Golden%20Sun" in url:
        return png(256, 256, (40, 120, 200), (10, 20, 60), "GS")
    raise Http404()


class FakeApp(tk.Tk):
    backup_dir = bk
    opened = []
    cfg = {"ra_rom_dirs": [str(roms)]}
    titles = None
    def resolve(self, emu):
        return {"RetroArch": {"Saves": saves, "States": states},
                "DuckStation": {"Memory Cards": T / "duck"}}.get(emu, {})
    def open_folder(self, p): self.opened.append(Path(p))


class Dialog(U.RASaveDialog):
    http_get = staticmethod(fake_get)
    art_cache = T / "artcache"
    dirs = []
    files = []
    def ask_directory(self, title, initial=None): return self.dirs.pop(0) if self.dirs else None
    def ask_open_file(self, title): return self.files.pop(0) if self.files else None
    def ask_open_files(self, title): return [self.files.pop(0)] if self.files else []
    def rom_dirs(self): return [Path(d) for d in self.custom_rom_dirs()]
    def esde_media(self): return []


app = FakeApp(); app.geometry("10x10+0+0")
d = Dialog(app)
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
seen = {}


def auto_answer(choice, title_has="", shot_name=None):
    def tick():
        o = v.overlay
        if o is not None and title_has in o.title:
            keys = [k for k, _l in o.options]
            if choice in keys:
                if shot_name:
                    shot(shot_name)
                answers.append(o.title)
                seen[title_has] = (o.message, [l for _k, l in o.options])
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


assert pump(4, lambda: v.running and v.items[0])
home = [it.title for it in v.items[0]]
assert v.mode == "systems" and home[-1] == "Backups" and home[-2] == "Other games", home
assert {"Game Boy Advance", "PlayStation", "SNES"} <= set(home) and v.tab_items() == [("Systems", True)]
gba = next(i for i, it in enumerate(v.items[0]) if it.title == "Game Boy Advance")
v.move(gba); pump(0.3)
shot("ra_systems")
v.act("ok"); pump(0.3)
assert v.mode == "games" and v.cur == "gba"
assert {it.title for it in v.items[0]} == {"Metroid Fusion (USA)", "Golden Sun (USA)"}, [it.title for it in v.items[0]]
tabs = v.tab_items()
assert ("GBA", True) in tabs and len(tabs) >= 18, tabs
assert v._tab_more[0] or v._tab_more[1], "tabs that don't fit are hinted with ‹ ›"
assert pump(4, lambda: v.art.get("gba|metroid fusion (usa)") and v.art.get("gba|golden sun (usa)"))
assert any("Golden%20Sun" in u for u in got) and not any("Metroid%20Fusion" in u for u in got), got[:5]
assert list((T / "artcache").glob("*.png")), "downloaded art cached"
assert v.items[0][idx("Golden Sun")].scattered
v.move(idx("Metroid Fusion")); pump(0.4)
assert v.canvas.find_withtag("big"), "a state screenshot in the panel"
first = v.slide
assert pump(4, lambda: v.slide != first), "screenshots cycle"
shot("ra_console")
v.act("next"); pump(0.2)
assert v.mode == "games" and v.cur != "gba"
v.act("prev"); pump(0.2)
assert v.cur == "gba" and v.items[0][v.cursor[0]].title.startswith("Metroid Fusion"), "cursor remembered per system"
v.act("right"); v.act("right"); v.act("right"); pump(0.1)
assert v.cur == "gba", "moving right off the grid doesn't change system"
v.act("back"); pump(0.2)
assert v.mode == "systems" and v.items[0][v.cursor[0]].title == "Game Boy Advance", "B goes back, onto the same system"
other = next(i for i, it in enumerate(v.items[0]) if it.title == "Other games")
v.move(other); v.act("ok"); pump(0.2)
assert v.cur == "" and any(it.orphan for it in v.items[0])
v.act("view"); pump(0.2)
assert v.mode == "systems"
print("1. systems first; A opens one; tabs per system with LB/RB, cursor remembered; B and View go back; box art: ok")

v.move(gba); v.act("ok"); pump(0.2)
v.move(idx("Metroid Fusion")); v.act("ok")
assert v.overlay.options[0][1] == "Back up this game"
pick("backup"); pump(0.5)
assert "Backed up 1 game" in v.toast_text
b = R.list_backups(bk)
assert len(b) == 1 and len(b[0].files) == 7, [p.name for p, *_ in b[0].files]
v.act("ok")
assert any("Restore a backup… (1)" in l for k, l in v.overlay.options), "the game's own backups in its menu"
v.act("back"); v.act("back"); pump(0.2)
bt = next(i for i, it in enumerate(v.items[0]) if it.kind == "backups")
v.move(bt); v.act("ok"); pump(0.2)
assert v.mode == "backups" and v.side == 1 and v.items[1][0].title == "Metroid Fusion (USA)"
shot("ra_backups")
v.act("back"); pump(0.2)
print("2. backing up a game into a dated backup; Backups on the systems screen: ok")

v.move(gba); v.act("ok"); v.move(idx("Metroid Fusion")); v.act("ok")
auto_answer("ok", "Close RetroArch")
auto_answer("yes", "Delete")
pick("delete"); pump(0.5)
assert not (saves / "Metroid Fusion (USA).srm").exists() and list((bk / R.SAFETY_NAME).rglob("Metroid Fusion (USA).srm"))
assert answers[:2] == ["Close RetroArch first", "Delete"]
v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.kind == "backups")); v.act("ok"); pump(0.2)
v.move(0); v.act("ok"); pick("restore"); pump(0.5)
assert (saves / "Metroid Fusion (USA).srm").read_bytes() == b"MF" * 4000 and "Restored 7 files" in v.toast_text
v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.title == "Game Boy Advance")); v.act("ok"); pump(0.2)
v.move(idx("Metroid Fusion")); v.act("ok")
auto_answer("0", "Restore Metroid"); auto_answer("skip", "Replace the saves")
pick("restore-one"); pump(0.5)
assert answers[-1] == "Replace the saves in RetroArch?"
print("3. delete (kept in the safety folder), restore from Backups and from the game's menu: ok")

v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.title == "Other games")); v.act("ok"); pump(0.2)
v.move(idx("Pokemon Emerald")); v.act("ok")
auto_answer("0", "Which game is this?", shot_name="ra_rename")
pick("rename"); pump(0.5)
assert "Pokemon - Emerald Version" in seen["Which game is this?"][1][0]
assert (saves / "Pokemon - Emerald Version (USA, Europe).srm").read_bytes() == b"PE"
assert not any("Pokemon" in it.title for it in v.items[0]), "it moved to Game Boy Advance"
print("4. renaming a save to match its renamed game file: ok")

v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.title == "Game Boy Advance")); v.act("ok"); pump(0.2)
assert any("Pokemon" in it.title for it in v.items[0])
v.act("menu")
assert any("in GBA" in l for k, l in v.overlay.options), [l for k, l in v.overlay.options]
auto_answer("go", "Tidy scattered saves"); pick("tidy"); pump(0.5)
assert "Golden Sun" in seen["Tidy scattered saves"][0]
assert (saves / "Golden Sun (USA).srm").read_bytes() == b"GS-newer"
assert any(p.read_bytes() == b"GS" * 1000 for p in (bk / R.SAFETY_NAME).rglob("Golden Sun (USA).srm"))
v.act("menu"); auto_answer("ok", "Tidy scattered saves"); pick("tidy"); pump(0.4)
assert "Every save is where" in seen["Tidy scattered saves"][0]
print("5. tidying this system: the newer scattered copy moved in, the older kept as a safety copy: ok")

v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.title == "PlayStation")); v.act("ok"); pump(0.2)
v.move(idx("Spyro")); v.act("ok")
auto_answer("0", "Copy memory card to DuckStation"); pick("ps1-out"); pump(0.5)
opt0 = seen["Copy memory card to DuckStation"][1][0]
assert opt0.startswith("Spyro the Dragon_1.mcd") and "replace" in opt0, opt0
assert (T / "duck/Spyro the Dragon_1.mcd").read_bytes() == bytes(card.img) and "DuckStation" in v.toast_text
blank = ps1card.Card.blank(); put(T / "duck/Other_1.mcd", bytes(blank.img), age=-10)
v.act("ok")
auto_answer("0", "Use a memory card from DuckStation"); auto_answer("yes", "Replace the card")
pick("ps1-in"); pump(0.6)
assert "Other_1.mcd" in seen["Use a memory card from DuckStation"][1][0]
assert (saves / "Spyro the Dragon (USA).srm").read_bytes() == bytes(blank.img)
print("6. PlayStation: card copied to DuckStation under its existing name, and a DuckStation card used in RetroArch: ok")

v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.title == "Game Boy Advance")); v.act("ok"); pump(0.2)
out = T / "exports"; out.mkdir()
d.dirs.append(out)
v.move(idx("Golden Sun")); v.act("ok"); pick("raw-out"); pump(0.4)
assert (out / "Golden Sun (USA).sav").read_bytes() == b"GS-newer"
put(T / "from-mgba.sav", b"mgba")
d.files.append(T / "from-mgba.sav")
v.act("ok"); auto_answer("yes", "Replace the save"); pick("raw-in"); pump(0.5)
assert (saves / "Golden Sun (USA).srm").read_bytes() == b"mgba"
print("7. Game Boy Advance: export as .sav and import a .sav: ok")

for i in range(40):
    put(T / "duck" / f"Card {i:02d}_1.mcd", bytes(blank.img))


def scroll_check():
    o = v.overlay
    if o is None or "Use a memory card" not in o.title:
        app.after(60, scroll_check); return
    for _ in range(len(o.options) - 1):
        v.act("down")
    seen["scroll"] = (o.top, o.boxes[-1][0], len(o.options))
    shot("ra_scroll")
    v.act("back")


v.act("back"); pump(0.2)
v.move(next(i for i, it in enumerate(v.items[0]) if it.title == "PlayStation")); v.act("ok"); pump(0.2)
v.move(idx("Spyro")); v.act("ok"); app.after(60, scroll_check); pick("ps1-in"); pump(0.4)
top, last, n = seen["scroll"]
assert top > 0 and last == n - 1, f"the menu scrolls to its last option {seen['scroll']}"
v.act("menu"); pick("where")
assert "retroarch.cfg" in v.overlay.message and str(saves) in v.overlay.message
v.act("ok")
v.act("menu"); pick("art"); assert app.cfg["ra_download_art"] is False
v.act("menu"); pick("open"); assert app.opened
d.destroy(); pump(0.2)
print("9. long menus scroll; where saves are kept; the box art switch; open folder: ok")

# RetroArch keeping its saves somewhere else than the folders the app syncs
synced = T / "synced"
put(synced / "saves" / "Metroid Fusion (USA).srm", b"old synced", age=9999); (synced / "states").mkdir(parents=True)
own = T / "home/.config/retroarch"
put(own / "saves" / "mGBA" / "Metroid Fusion (USA).srm", b"newer, RetroArch's own")
put(own / "saves" / "mGBA" / "Golden Sun (USA).srm", b"only here"); (own / "states").mkdir(parents=True)
(own / "retroarch.cfg").write_text('savefile_directory = ":/saves"\nsavestate_directory = ":/states"\n'
                                   'sort_savefiles_enable = "true"\n')


class App2(FakeApp):
    cfg = {"ra_rom_dirs": [str(roms)]}
    def resolve(self, emu):
        return {"RetroArch": {"Saves": synced / "saves", "States": synced / "states"}}.get(emu, {})


app.destroy()
app = App2(); app.geometry("10x10+0+0")
d = Dialog(app); v = d.view
assert pump(4, lambda: v.running and v.items[0])
assert v.items[0][0].kind == "notice" and v.items[0][v.cursor[0]].kind == "notice", [it.title for it in v.items[0]]
shot("ra_notice")
answers.clear()
auto_answer("fix", "isn't using these saves"); auto_answer("ok", "Close RetroArch")
v.act("ok"); pump(0.8)
assert answers == ["RetroArch isn't using these saves", "Close RetroArch first"], answers
assert R.read_cfg(own / "retroarch.cfg")["savefile_directory"] == str(synced / "saves")
assert (synced / "saves" / "Metroid Fusion (USA).srm").read_bytes() == b"newer, RetroArch's own"
assert (synced / "saves" / "Golden Sun (USA).srm").read_bytes() == b"only here"
assert v.items[0] and v.items[0][0].kind != "notice", "the notice is gone once fixed"
d.destroy(); pump(0.2)
print("11. RetroArch saving elsewhere: a notice first on the systems screen, and Fix it moves RetroArch over: ok")

import emu_save_manager as esm
esm.App.open_folder = lambda self, p: None
main = esm.App()
main.cfg["overrides"] = {"RetroArch::Saves": str(saves), "RetroArch::States": str(states)}
labels = [main.cards_menu.entrycget(i, "label") for i in range(main.cards_menu.index("end") + 1)
          if main.cards_menu.type(i) != "separator"]
assert labels[-1] == "RetroArch Save Manager…", labels
main.open_ra_saves(); pump(1.5)
dlg = [w for w in main.winfo_children() if isinstance(w, U.RASaveDialog)]
assert dlg and pump(3, lambda: dlg[0].view.items[0])
main.destroy()
print("10. Memory Cards → RetroArch Save Manager… opens it: ok")
app.destroy()
print("gui_rasaves OK")
