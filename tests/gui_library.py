"""ROM Library window: first run opens Settings, the library lists by system with status, search and
filters work, check out / check in / add to library run through the queue, favourites, Deck layout.
Run with xvfb-run -a.  ESM_SHOTS=dir saves screenshots (needs ImageMagick's import)."""
import os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_gui"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import romlibrary as L, libqueue as Q, ui_library as U

def mk(path, size):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f: f.truncate(size)          # sparse: big sizes without using disk
    return p

lib, local = T / "NUC/Games/roms", T / "Emulation/roms"
GB, MB = 1024**3, 1024**2
games = {"ps2": [("Okami (USA).iso", 4.2*GB), ("God of War (USA).iso", 4.4*GB), ("Shadow of the Colossus (USA).iso", 2.9*GB),
                 ("Ratchet & Clank (USA).iso", 3.1*GB), ("Kingdom Hearts (USA).iso", 3.4*GB)],
         "gc": [("Pikmin (USA).rvz", 400*MB), ("Metroid Prime (USA).rvz", 900*MB), ("Wind Waker (USA).rvz", 1.1*GB)],
         "xbox": [("Halo (USA).iso", 3.8*GB), ("Jet Set Radio Future (USA).iso", 4.1*GB)],
         "xbox360": [("Halo 3 (USA).iso", 6.8*GB)],
         "switch": [("Zelda BOTW (USA).nsp", 14*GB)],
         "neo geo": [("mslug.zip", 20*MB), ("kof98.zip", 45*MB)]}
for sysdir, lst in games.items():
    for name, size in lst:
        mk(lib / sysdir / name, int(size))
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_GAME/USRDIR/EBOOT.BIN", int(12*GB))
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_DISC.SFB", 1024)
mk(local / "ps2/Okami (USA).iso", int(4.2*GB))
mk(local / "ps2/My Homebrew.iso", 7*MB)
mk(local / "gc/Pikmin (USA).rvz", 100*MB)                 # half copied

U.messagebox.askokcancel = lambda *a, **k: True
U.messagebox.showinfo = lambda *a, **k: None
U.messagebox.showwarning = lambda *a, **k: print("WARN:", a[1][:80])
errors = []
U.messagebox.showerror = lambda *a, **k: errors.append(a[1])
shots = os.environ.get("ESM_SHOTS")

def shot(app, name):
    if shots and shutil.which("import"):
        app.update(); time.sleep(0.3); app.update()
        subprocess.run(["import", "-window", "root", os.path.join(shots, name)], check=False)

def pump(app, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)

def wait(app, cond, sec=30):
    end = time.time() + sec
    while time.time() < end:
        pump(app, 0.1)
        if cond():
            pump(app, 0.7)                     # let the window's next refresh (every 0.3 s) run
            return True
    return False

# 1. first run: no library set -> Settings opens; fill it in and save
app = U.LibraryApp(cfg=dict(L.load_config(), view="list"))
pump(app, 0.6)
dlg = [w for w in app.winfo_children() if isinstance(w, U.SettingsDialog)]
assert dlg, "first run opens Settings"
d = dlg[0]
d.v_kind.set("folder"); d._kind(); d.v_folder.set(str(lib)); d.v_local.set(str(local)); d.v_label.set("msi")
d.test(); assert wait(app, lambda: "Connected" in d.test_label.cget("text")), d.test_label.cget("text")
print("1. first run opens Settings; Test:", d.test_label.cget("text"))
shot(app, "settings.png")
d.v_local.set(str(lib / "ps2")); d.save(); pump(app)
assert errors and "different folders" in errors[-1]; errors.clear()
d.v_local.set(str(local)); d.save()
assert wait(app, lambda: app.lib and len(app.lib.items) == 15 and not app._indexing), app.lib and len(app.lib.items)
assert L.load_config()["library"] == str(lib)
print("   library read:", app.status.cget("text"))

# 2. lists, statuses, systems
rows = app.tree.get_children()
assert len(rows) == 16, len(rows)                        # 15 in library + 1 only here
def status(k): return app.tree.set(k, "status")
assert status("ps2/Okami (USA).iso").startswith("✔"), status("ps2/Okami (USA).iso")
assert status("gc/Pikmin (USA).rvz").startswith("◐ Incomplete"), status("gc/Pikmin (USA).rvz")
assert status("ps2/My Homebrew.iso").startswith("Only on this machine")
assert status("ps2/God of War (USA).iso") == "In library"
assert app.tree.set("neogeo/mslug.zip", "system") == "Neo Geo"
sysrows = {app.sys_tree.set(i, "name"): app.sys_tree.set(i, "count") for i in app.sys_tree.get_children()}
assert sysrows["PlayStation 2"] == "2 of 6", sysrows
print("2. statuses and systems:", {k: sysrows[k] for k in ("All systems", "PlayStation 2", "GameCube")})
shot(app, "library-pc.png")

# 3. search, system filter, show filter
app.v_search.set("halo"); pump(app, 0.5)
assert set(app.tree.get_children()) == {"xbox/Halo (USA).iso", "xbox360/Halo 3 (USA).iso"}
app.v_search.set(""); app.sys_tree.selection_set("gc"); pump(app, 0.4)
assert all(k.startswith("gc/") for k in app.tree.get_children()) and len(app.tree.get_children()) == 3
app.sys_tree.selection_set(U.ALL); pump(app, 0.2); app.v_filter.set("On this machine"); app.fill_games()
assert set(app.tree.get_children()) == {"ps2/Okami (USA).iso", "gc/Pikmin (USA).rvz", "ps2/My Homebrew.iso"}
app.v_filter.set(U.FILTERS[0]); app.fill_games()
print("3. search, system and Show filters: ok")

# 4. sorting by size, biggest first
app._sort("size"); first = app.tree.get_children()[0]
assert first == "switch/Zelda BOTW (USA).nsp", first
app._sort("system")
print("4. sort by size:", app.tree.set(first, "title"))

# 5. buttons follow the selection
app.tree.selection_set(["ps2/God of War (USA).iso"]); pump(app, 0.1)
assert app.btn_out.instate(["!disabled"]) and app.btn_in.instate(["disabled"]) and app.btn_add.instate(["disabled"])
app.tree.selection_set(["ps2/My Homebrew.iso"]); pump(app, 0.1)
assert app.btn_add.instate(["!disabled"]) and app.btn_out.instate(["disabled"]) and app.btn_fav.instate(["disabled"])
print("5. buttons enabled to match the selection: ok")

# 6. check out two games and finish the half-copied one; the rows show progress, then ✔
app.tree.selection_set(["ps2/God of War (USA).iso", "gc/Pikmin (USA).rvz", "neogeo/mslug.zip"]); app.check_out()
assert app.q.busy()
assert wait(app, lambda: not app.q.busy(), 120), [(j.title, j.state, j.error) for j in app.q.jobs]
for k in ("ps2/God of War (USA).iso", "gc/Pikmin (USA).rvz", "neogeo/mslug.zip"):
    assert status(k).startswith("✔"), (k, status(k))
assert (local / "neogeo/mslug.zip").is_file()
qrows = [app.qtree.set(i, "status") for i in app.qtree.get_children()]
assert qrows == ["Done"] * 3, qrows
assert "3 game(s) checked out" in app.space_label.cget("text") or "4 game(s) checked out" in app.space_label.cget("text"), app.space_label.cget("text")
print("6. checked out via the queue:", app.space_label.cget("text"))
shot(app, "after-checkout.png")

# 7. check in, add to library, favourite
app.tree.selection_set(["ps2/Okami (USA).iso"]); app.check_in()
assert wait(app, lambda: not app.q.busy()) and status("ps2/Okami (USA).iso") == "In library"
assert not (local / "ps2/Okami (USA).iso").exists() and (lib / "ps2/Okami (USA).iso").exists()
app.tree.selection_set(["ps2/My Homebrew.iso"]); app.add_to_library()
assert wait(app, lambda: not app.q.busy()) and status("ps2/My Homebrew.iso").startswith("✔")
assert (lib / "ps2/My Homebrew.iso").exists()
app.tree.selection_set(["gc/Metroid Prime (USA).rvz", "ps2/Okami (USA).iso"]); app.toggle_favourite()
assert wait(app, lambda: app.tree.set("gc/Metroid Prime (USA).rvz", "fav") == "★")
assert app.lib.is_favourite("ps2/Okami (USA).iso")
app.v_filter.set("Favourites"); app.fill_games(); assert len(app.tree.get_children()) == 2
app.v_filter.set(U.FILTERS[0]); app.fill_games(); app.q.clear_finished(); pump(app, 0.4)
print("7. check in, add to library, favourites: ok")

# 8. another machine sees what this one has checked out
app.destroy()
other_local = T / "deck/Emulation/roms"; other_local.mkdir(parents=True)
cfg = L.load_config(); cfg["local_roms"] = str(other_local); cfg["machine_label"] = "Steam Deck"
import romlibrary
romlibrary.machine_id = lambda: "steamdeck"
os.environ["ESM_DECK"] = "1"
app = U.LibraryApp(cfg=cfg)
assert wait(app, lambda: app.lib and app.lib.items and not app._indexing)
s = app.tree.set("ps2/God of War (USA).iso", "status")
assert s == "In library · on msi", s
print("8. the other machine shows:", s)
app.destroy()

print("ALL OK")
