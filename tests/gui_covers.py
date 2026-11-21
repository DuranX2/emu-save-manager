"""ROM Library cover view: the grid shows box art with status badges, selecting shows details, keyboard
moves the selection, the Artwork window imports from ES-DE, and the Deck layout fits.
Run with xvfb-run -a.  ESM_SHOTS=dir saves screenshots."""
import io, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECK = "--deck" in sys.argv
if not DECK and os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run") and "--no-deck" not in sys.argv:
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__, "--deck"],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    if r.returncode: sys.exit(r.returncode)
T = Path("/tmp/romlib_covers" + ("_deck" if DECK else "")); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L, libmeta as M, ui_library as U
from PIL import Image, ImageDraw

PALETTE = [(166, 60, 60), (52, 101, 164), (78, 154, 6), (117, 80, 123), (193, 125, 17), (32, 120, 130), (90, 90, 90)]
def card(title, i, w=600, h=840):
    im = Image.new("RGB", (w, h), PALETTE[i % len(PALETTE)]); d = ImageDraw.Draw(im)
    d.rectangle([20, 20, w - 20, h - 20], outline=(255, 255, 255), width=6)
    y = h // 3
    for word in title.split():
        d.text((50, y), word, fill=(255, 255, 255), font_size=64); y += 80
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
def shotimg(i):
    im = Image.new("RGB", (640, 480), (30 + i * 20 % 200, 40, 70)); d = ImageDraw.Draw(im)
    for k in range(8): d.rectangle([40 + k * 70, 300 - k * 25, 90 + k * 70, 400], fill=(200, 200 - k * 20, 80))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
def mk(path, size=1000):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f: f.truncate(size)
    return p

GB = 1024 ** 3
lib, local = T / "NUC/roms", T / "Emulation/roms"
games = [("ps2", "Okami (USA).iso", "Okami", "Action-Adventure", "2006"), ("ps2", "God of War (USA).iso", "God of War", "Action", "2005"),
         ("ps2", "Shadow of the Colossus (USA).iso", "Shadow of the Colossus", "Adventure", "2005"),
         ("ps2", "Kingdom Hearts (USA).iso", "Kingdom Hearts", "Action RPG", "2002"),
         ("ps2", "Ratchet & Clank (USA).iso", "Ratchet & Clank", "Platformer", "2002"),
         ("gc", "Metroid Prime (USA).rvz", "Metroid Prime", "Action", "2002"), ("gc", "Pikmin (USA).rvz", "Pikmin", "Strategy", "2001"),
         ("gc", "Wind Waker (USA).rvz", "The Legend of Zelda: The Wind Waker", "Adventure", "2002"),
         ("xbox", "Halo (USA).iso", "Halo: Combat Evolved", "Shooter", "2001"),
         ("xbox", "Jet Set Radio Future (USA).iso", "Jet Set Radio Future", "Action", "2002"),
         ("xbox360", "Halo 3 (USA).iso", "Halo 3", "Shooter", "2007"), ("switch", "Zelda BOTW (USA).nsp", None, None, None),
         ("ps3", "Demon's Souls [BLUS30443]", "Demon's Souls", "Action RPG", "2009")]
for i, (s, f, *_x) in enumerate(games):
    if s == "ps3": mk(lib / s / f / "PS3_GAME/USRDIR/EBOOT.BIN", int(12 * GB))
    else: mk(lib / s / f, int((1 + i % 5) * GB))
mk(local / "ps2/Okami (USA).iso", int(1 * GB)); mk(local / "gc/Pikmin (USA).rvz", int(1 * GB))      # sizes match (1+i%5)
mk(local / "ps2/God of War (USA).iso", int(0.5 * GB))                                                # incomplete

runner = cloudsync.RcloneRunner(shutil.which("rclone"), L.rclone_conf(), lambda m: None)
lb = L.Library(runner, str(lib), str(local), machine="msi", label="msi"); lb.refresh()
store = M.MetaStore(lb)
# most games already have information in the library (as if another machine looked them up) ...
for i, (s, f, title, genre, year) in enumerate(games):
    if title and s not in ("gc",):
        key = f"{L.canonical(s)}/{f}"
        store.put(key, {"title": title, "genre": genre, "year": year, "date": f"{year}-03-22",
                        "developer": "Some Studio", "publisher": "Some Publisher", "players": "1",
                        "desc": f"{title} is a game. " * 8, "rating": 80 + i, "source": "screenscraper"},
                  {"cover": card(title, i), "screenshot": shotimg(i)})
store.flush()
# ... and the GameCube ones only in ES-DE on this machine
home = T / "home"
xml = "".join(f"<game><path>./{f}</path><name>{title}</name><desc>From ES-DE: {title}.</desc><genre>{genre}</genre>"
              f"<releasedate>{year}0101T000000</releasedate></game>" for s, f, title, genre, year in games if s == "gc")
mk(home / "ES-DE/gamelists/gc/gamelist.xml", 0).write_text(f"<gameList>{xml}</gameList>")
for i, (s, f, title, *_x) in enumerate(games):
    if s == "gc":
        p = home / "Emulation/tools/downloaded_media/gc/covers" / (os.path.splitext(f)[0] + ".png")
        p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(card(title, i + 3))
cloudsync.emudeck_roots = lambda: [home / "Emulation"]
M.cloudsync.emudeck_roots = cloudsync.emudeck_roots

U.messagebox.showinfo = lambda *a, **k: None
U.messagebox.askokcancel = lambda *a, **k: True
shots = os.environ.get("ESM_SHOTS")
def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
def wait(cond, sec=30):
    end = time.time() + sec
    while time.time() < end:
        pump(0.1)
        if cond(): pump(0.5); return True
    return False
def shot(name):
    if shots and shutil.which("import"):
        pump(0.6); subprocess.run(["import", "-window", "root", os.path.join(shots, name)])

M.MEDIA_CACHE = T / "home/.emu_rom_library/media_pc"         # this machine hasn't seen the pictures yet
cfg = L.load_config(); cfg.update(library=str(lib), local_roms=str(local), machine_label="Steam Deck" if DECK else "msi")
app = U.LibraryApp(cfg=cfg)
assert wait(lambda: app.lib and len(app.lib.items) == 13 and not app._indexing)
if DECK:
    app.state("normal"); app.geometry("1280x760+0+0"); pump(0.8); app._place_sashes()
pump(0.5)
assert app.view == "covers" and app.grid_view.winfo_ismapped()
g = app.grid_view
assert len(g.keys) == 13
# 1. covers arrive from the library and are drawn
assert wait(lambda: sum(1 for k in g.keys if app.images.images.get((k, "cover", app.tile_w, int(app.tile_w * 1.4)))) >= 6, 40), \
    sum(1 for k in g.keys if app.images.images.get((k, "cover", app.tile_w, int(app.tile_w * 1.4))))
pump(0.5)
imgs = [i for i in g.canvas.find_all() if g.canvas.type(i) == "image"]
print(f"1. {g.cols} columns; {len(imgs)} covers drawn on screen")
assert imgs and g.cols >= 3
assert app.display_title("xbox/Halo (USA).iso") == "Halo: Combat Evolved"
# 2. select a game: details panel shows information and the right buttons
g.set_selection(["ps2/God of War (USA).iso"]); pump(0.4)
assert wait(lambda: app.images.images.get(("ps2/God of War (USA).iso", "cover", app.details.cover_w, app.details.cover_h)))
texts = [w.cget("text") for w in app.details.inner.winfo_children() if isinstance(w, U.ttk.Label)]
assert "God of War" in texts and any("Incomplete" in t for t in texts), texts
btns = [w.cget("text") for f in app.details.inner.winfo_children() if isinstance(f, U.ttk.Frame)
        for w in f.winfo_children() if isinstance(w, U.ttk.Button)]
assert "⬇ Check out" in btns and "⬆ Check in" in btns, btns
print("2. details panel:", [t for t in texts if t][:3], btns)
shot("covers-deck.png" if DECK else "covers-pc.png")
# 3. keyboard: arrows move the selection; multi-select summary
g.canvas.focus_set(); g.set_selection(["gc/Metroid Prime (USA).rvz"]); pump(0.2)
i0 = g.keys.index("gc/Metroid Prime (USA).rvz")
g.canvas.event_generate("<Right>"); pump(0.2); assert g.sel == [g.keys[i0 + 1]]
g.canvas.event_generate("<Down>"); pump(0.2); assert g.sel == [g.keys[i0 + 1 + g.cols]]
g.set_selection(g.keys[:3]); pump(0.3)
assert any("3 games selected" in w.cget("text") for w in app.details.inner.winfo_children() if isinstance(w, U.ttk.Label))
print("3. arrow keys move the selection; several selected shows a summary: ok")
# 4. search matches game information (genre)
app.v_search.set("shooter"); pump(0.6)
assert set(g.keys) == {"xbox/Halo (USA).iso", "xbox360/Halo 3 (USA).iso"}, g.keys
app.v_search.set(""); pump(0.5)
print("4. search finds games by genre from their information: ok")
# 5. Artwork window: import the GameCube games from ES-DE (others are skipped: they have information)
assert not app.meta.get("gc/Pikmin (USA).rvz")
app.open_artwork(); pump(0.5)
d = app.artwork_dialog
assert d and "described" in str([w.cget("text") for w in d.winfo_children()[0].winfo_children() if isinstance(w, U.ttk.Label)])
d.v_ss.set(False); d.v_lr.set(False); d.start()
assert wait(lambda: not app.scraping() and app.meta.get("gc/Pikmin (USA).rvz").get("media"), 60)
assert app.meta.get("gc/Pikmin (USA).rvz")["desc"] == "From ES-DE: Pikmin."
assert (lib / ".library/meta/gc.json").is_file(), "imported information is written to the library"
assert "Done" in app.scrape_status, app.scrape_status
print("5. Artwork window imported from ES-DE:", app.scrape_status)
d.close(); pump(0.3)
# 6. list view still works and keeps the selection
g.set_selection(["xbox/Halo (USA).iso"]); app.set_view("list"); pump(0.4)
assert app.tree.selection() == ("xbox/Halo (USA).iso",) and app.tree.set("xbox/Halo (USA).iso", "title") == "Halo (USA)"
app.set_view("covers"); pump(0.4)
assert g.sel == ["xbox/Halo (USA).iso"]
print("6. switching Covers / List keeps the selection: ok")
if DECK:
    bottom = app.space_label.winfo_rooty() + app.space_label.winfo_height()
    assert bottom <= 760, bottom
    print(f"7. Deck: fits 1280x760, {g.cols} columns of covers")
g.set_selection(["gc/Wind Waker (USA).rvz"]); pump(0.3)
assert wait(lambda: app.images.images.get(("gc/Wind Waker (USA).rvz", "cover", app.details.cover_w, app.details.cover_h)))
shot("covers-deck-2.png" if DECK else "covers-pc-2.png")
app.destroy()
print("ALL OK")
