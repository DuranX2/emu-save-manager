"""ROM Library with system folders that are links to SD cards (the Deck: EmuDeck on the internal
drive, Emulation/roms/<system> -> a folder on one of several cards). Linked folders are read, a link
looping back is read once, a card that isn't inserted shows as such (games checked out to it stay
checked out), checking out to it gives a clear message, and space is worked out per drive.
Run with xvfb-run -a (part 5 opens the Library window)."""
import json, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_links"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L, libqueue as Q

checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


def mk(path, size=1000):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(size)); return p


lib = T / "NUC/Games/roms"
for rel in ("snes/Platformer (USA).sfc", "gc/Plumber Party (USA).rvz", "gc/Island Quest (USA).rvz",
            "ps2/Space Game (USA).chd", "ps2/Big Adventure (USA).chd"):
    mk(lib / rel)
roms = T / "home/Emulation/roms"
card_n, card_s = T / "media/NINTENDO/roms", T / "media/SONY/roms"
mk(roms / "snes/Platformer (USA).sfc")                         # a real folder on the internal drive
mk(card_n / "gc/Plumber Party (USA).rvz")
shutil.copy(lib / "gc/Plumber Party (USA).rvz", card_n / "gc/Plumber Party (USA).rvz")
mk(card_s / "ps2/Space Game (USA).chd")
shutil.copy(lib / "ps2/Space Game (USA).chd", card_s / "ps2/Space Game (USA).chd")
shutil.copy(lib / "snes/Platformer (USA).sfc", roms / "snes/Platformer (USA).sfc")
os.symlink(card_n / "gc", roms / "gc")
os.symlink(card_s / "ps2", roms / "ps2")
os.symlink(roms, card_n / "gc/back to roms")                  # a loop: must not be read forever

# 1. linked system folders are read, the loop only once
entries = L.list_local(roms)
paths = sorted(p for p, *_ in entries)
ok("gc/Plumber Party (USA).rvz" in paths and "ps2/Space Game (USA).chd" in paths, paths)
ok(not any(p.startswith("gc/back to roms/") for p in paths), "the loop is read once")
ok(L.missing_links(roms) == {}, "both cards inserted")
runner = cloudsync.RcloneRunner(shutil.which("rclone"), "", lambda m: None)
lb = L.Library(runner, str(lib), str(roms), machine="deck", label="Steam Deck")
lb.refresh()
ok(lb.status("gc/Plumber Party (USA).rvz")[0] == "here" and lb.status("ps2/Space Game (USA).chd")[0] == "here",
   (lb.status("gc/Plumber Party (USA).rvz"), lb.status("ps2/Space Game (USA).chd")))
ok(lb.local_sysdirs.get("gc") == "gc" and lb.system_path("gc") == roms / "gc")
for k in ("gc/Plumber Party (USA).rvz", "ps2/Space Game (USA).chd", "snes/Platformer (USA).sfc"):
    lb.record(k, True)
print("1. linked folders read:", sorted({p.split('/')[0] for p in paths}))

# 2. checking out into a linked folder puts the game on the card
q = Q.TransferQueue(lb, log=lambda m: None, autostart=False)
q.add("checkout", ["gc/Island Quest (USA).rvz"])
q.start()
end = time.time() + 30
while time.time() < end and any(j.state in ("queued", "running") for j in q.snapshot()):
    time.sleep(0.2)
j = q.snapshot()[-1]
ok(j.state == "done" and (card_n / "gc/Island Quest (USA).rvz").is_file(), (j.state, j.error))
lb.refresh_local()
ok(lb.status("gc/Island Quest (USA).rvz")[0] == "here", "still 'here' after reading the folder again")
print("2. checked out through the link onto the card:", j.state)

# 3. the Sony card is taken out
os.rename(T / "media/SONY", T / "media/SONY-out")
lb.refresh_local()
ok(set(lb.offline) == {"ps2"} and lb.offline["ps2"][1] == str(card_s / "ps2"), lb.offline)
st, detail = lb.status("ps2/Space Game (USA).chd")
ok(st == "card_out" and detail == str(card_s / "ps2"), (st, detail))
ok(lb.status("ps2/Big Adventure (USA).chd")[0] == "not_here", "never checked out: just in the library")
ok(lb.system_space("ps2") is None and lb.system_space("gc") is not None)
lb._publish_mine()
pub = json.loads((lib / ".library/machines/deck.json").read_text())
ok("ps2/Space Game (USA).chd" in pub["items"], "other machines still see it checked out here")
n, size = lb.checked_out_size()
ok(n == 4, (n, size))
q.add("checkout", ["ps2/Big Adventure (USA).chd"])
end = time.time() + 20
while time.time() < end and any(j.state in ("queued", "running") for j in q.snapshot()):
    time.sleep(0.2)
j = q.snapshot()[-1]
ok(j.state == "failed" and "Insert that SD card" in (j.error or "") and "isn't there" in j.error, (j.state, j.error))
ok(not (T / "media/SONY").exists(), "nothing was created where the card should be")
q.stop(wait=3)
print("3. card out:", st, "|", j.error)

# 4. the card goes back in: everything is as it was
os.rename(T / "media/SONY-out", T / "media/SONY")
lb.refresh_local()
ok(lb.offline == {} and lb.status("ps2/Space Game (USA).chd")[0] == "here")
print("4. card back in: 'here' again")

# 5. the window: per-drive space, card-out status and the space check per card
import ui_library as U
U.messagebox.showinfo = lambda *a, **k: None
warned = []
U.messagebox.showwarning = lambda *a, **k: warned.append(a[1])
os.rename(T / "media/SONY", T / "media/SONY-out")
devs = {"internal": 1, "NINTENDO": 2}
real_drive_of = L.drive_of
L.drive_of = lambda p: ((2, str(T / "media/NINTENDO")) if "NINTENDO" in os.path.realpath(p)
                        else (1, str(T / "home")))
real_space = L.disk_space
L.disk_space = lambda p: (64 * 2**30, 60 * 2**30, 4 * 2**30) if "NINTENDO" in str(p) else (512 * 2**30, 100 * 2**30, 412 * 2**30)
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(roms), "view": "list"})


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)


end = time.time() + 30
while time.time() < end and not (app.lib and len(app.lib.items) == 5 and not app._indexing):
    pump(0.1)
pump(0.5)
label = app.space_label.cget("text")
ok("412.0 GB free (internal)" in label and "4.0 GB free (NINTENDO)" in label and "card out: PlayStation 2" in label, label)
ok(app.tree.set("ps2/Space Game (USA).chd", "status").startswith("▣ On an SD card"), app.tree.set("ps2/Space Game (USA).chd", "status"))
app.sys_tree.selection_set("ps2"); pump(0.3)
ok("insert that SD card" in app.status.cget("text"), app.status.cget("text"))
app.sys_tree.selection_set(U.ALL); pump(0.3)
app.tree.selection_set(["ps2/Big Adventure (USA).chd"]); pump(0.1); app.check_out(); pump(0.2)
ok(warned and "isn't inserted" in warned[-1], warned)
big = lib / "gc/Huge Game (USA).rvz"
with open(big, "wb") as f: f.truncate(5 * 2**30)
app.lib.refresh(); app.fill_all(); pump(0.3)
app.tree.selection_set(["gc/Huge Game (USA).rvz"]); pump(0.1); app.check_out(); pump(0.2)
ok("Not enough space on the SD card \"NINTENDO\"" in warned[-1], warned[-1])
print("5. window:", label)
app.destroy()
L.drive_of, L.disk_space = real_drive_of, real_space
print(f"{checks} checks passed")
