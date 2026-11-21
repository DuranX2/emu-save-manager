"""ROM Library core: grouping files into games, system names, status, shared files on the library."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_core"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L

def mk(path, size=10):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x" * size); return p

lib = T / "library"
mk(lib / "ps2/Okami (USA).iso", 300)
mk(lib / "ps2/metadata.txt", 5)
mk(lib / "psx/Final Fantasy VII (USA).m3u", 3)
for d in (1, 2):
    mk(lib / f"psx/Final Fantasy VII (USA) (Disc {d}).cue", 1)
    mk(lib / f"psx/Final Fantasy VII (USA) (Disc {d}) (Track 1).bin", 100)
mk(lib / "psx/Final Fantasy VIII (USA).chd", 50)          # must not be claimed by FF VII's m3u
mk(lib / "psx/Crash (USA).cue", 1); mk(lib / "psx/Crash (USA) (Track 1).bin", 70); mk(lib / "psx/Crash (USA) (Track 2).bin", 7)
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_GAME/USRDIR/EBOOT.BIN", 500)
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_DISC.SFB", 1)
mk(lib / "neo geo/mslug.zip", 20)
mk(lib / "switch/USA/Zelda BOTW (USA).nsp", 400)
mk(lib / "switch/USA/Mario Odyssey (USA).xci", 350)
mk(lib / "gc/Pikmin (USA)/Pikmin (USA).rvz", 80)            # one game in its own folder
mk(lib / ".library/library.json", 2)

# 1. grouping
by = L.build_items(L.list_local(lib))
keys = {it.key: it for lst in by.values() for it in lst}
for k in sorted(keys): print("   ", k, keys[k].size, len(keys[k].files))
assert set(keys) == {"ps2/Okami (USA).iso", "psx/Final Fantasy VII (USA).m3u", "psx/Final Fantasy VIII (USA).chd",
                     "psx/Crash (USA).cue", "ps3/Demon's Souls [BLUS30443]", "neogeo/mslug.zip",
                     "switch/USA/Zelda BOTW (USA).nsp", "switch/USA/Mario Odyssey (USA).xci",
                     "gc/Pikmin (USA)"}, set(keys)
assert len(keys["psx/Final Fantasy VII (USA).m3u"].files) == 5 and keys["psx/Final Fantasy VII (USA).m3u"].size == 205
assert keys["psx/Crash (USA).cue"].size == 78
assert keys["ps3/Demon's Souls [BLUS30443]"].is_dir and keys["ps3/Demon's Souls [BLUS30443]"].size == 501
assert keys["psx/Final Fantasy VII (USA).m3u"].title == "Final Fantasy VII (USA)"
print("1. files grouped into games (m3u+cue+bin, folders, categories, ignored files): ok")

# 2. system names
assert L.canonical("neo geo") == "neogeo" and L.canonical("jaguar cd") == "atarijaguarcd"
assert L.canonical("PS2") == "ps2" and L.canonical("GameCube") == "gc" and L.system_name("gc") == "GameCube"
assert L.canonical("mystery") == "mystery"
assert L.is_remote("romlibrary:Games/roms") and not L.is_remote(r"\\NUC\Games\roms")
assert not L.is_remote("C:\\roms") and not L.is_remote("/mnt/roms") and not L.is_remote("E:")
assert L.join("romlibrary:", "ps2", "a.iso") == "romlibrary:ps2/a.iso"
assert L.join("romlibrary:Games/roms", ".library") == "romlibrary:Games/roms/.library"
assert L.same_or_nested(str(lib), str(lib / "ps2")) and not L.same_or_nested(str(lib), str(T / "local"))
print("2. system names and locations: ok")

# 3. a Library over rclone (a local folder works as an rclone location), with a local ROM folder
local = T / "Emulation/roms"
mk(local / "ps2/Okami (USA).iso", 300)                      # already here
mk(local / "psx/Final Fantasy VII (USA).m3u", 3)            # half here
mk(local / "psx/Final Fantasy VII (USA) (Disc 1).cue", 1); mk(local / "psx/Final Fantasy VII (USA) (Disc 1) (Track 1).bin", 40)
mk(local / "ps2/Homebrew Demo.iso", 9)                      # only on this machine
mk(local / "neogeo/kof98.zip", 5)
runner = cloudsync.RcloneRunner(shutil.which("rclone"), "", print)
lb = L.Library(runner, str(lib), str(local), machine="deck1", label="Steam Deck")
lb.refresh()
assert lb.status("ps2/Okami (USA).iso")[0] == "here"
st = lb.status("psx/Final Fantasy VII (USA).m3u"); assert st[0] == "partial", st
assert lb.status("ps2/Homebrew Demo.iso")[0] == "local_only"
assert lb.status("switch/USA/Zelda BOTW (USA).nsp")[0] == "not_here"
assert lb.local_dir_for("neogeo") == "neogeo" and lb.lib_dir_for("neogeo") == "neo geo"
assert lb.local_dir_for("gc") == "gc"
print("3. status here / partial / only here / not here; folder names per side: ok", st[1])

# 4. index cache
lb2 = L.Library(runner, str(lib), str(local)); assert lb2.load_cache() and set(lb2.items) == set(lb.items)
print("4. index cache reloads: ok")

# 5. shared files: favourites merge, each machine writes its own checkouts file
lb.record("ps2/Okami (USA).iso", True)
assert lb.set_favourite(["ps2/Okami (USA).iso"], True)
other = L.Library(runner, str(lib), str(T / "pc_roms"), machine="msi", label="msi")
other.refresh()
assert other.is_favourite("ps2/Okami (USA).iso")
assert other.elsewhere("ps2/Okami (USA).iso") == ["Steam Deck"], other.elsewhere("ps2/Okami (USA).iso")
assert other.set_favourite(["gc/Pikmin (USA)"], True)
lb.load_shared()
assert lb.is_favourite("ps2/Okami (USA).iso") and lb.is_favourite("gc/Pikmin (USA)")
assert (lib / ".library/machines/deck1.json").is_file() and (lib / ".library/machines/msi.json").is_file()
assert "ps2/Okami (USA).iso" not in other.items or other.status("ps2/Okami (USA).iso")[0] == "not_here"
print("5. favourites shared and merged; checkouts visible to the other machine: ok")

# 6. setup_share writes an smb remote with an obscured password, not the plain one
loc = L.setup_share(runner, "NUC", "Games", "roms", "player", "s3cret-pass")
assert loc == "romlibrary:Games/roms", loc
conf = L.OWN_RCLONE_CONF.read_text()
assert "type = smb" in conf and "host = NUC" in conf and "s3cret-pass" not in conf and "pass = " in conf
assert "s3cret" not in L.CONFIG_FILE.read_text() if L.CONFIG_FILE.exists() else True
before = L._conf_value(L.OWN_RCLONE_CONF, "romlibrary", "pass")
loc = L.setup_share(runner, "NUC2", "Games", "", "player", "")
assert L.OWN_RCLONE_CONF.read_text().count("[romlibrary]") == 1 and loc == "romlibrary:Games"
assert L._conf_value(L.OWN_RCLONE_CONF, "romlibrary", "pass") == before and L.share_has_password(), \
    "a blank password keeps the saved one"
assert L._conf_value(L.OWN_RCLONE_CONF, "romlibrary", "host") == "NUC2"
print("6. network share remote written, password obscured: ok")
print("ALL OK")
