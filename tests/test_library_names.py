"""System folder names: short and full names ('DC', 'Sega Dreamcast', 'MD', 'SMS', 'Nintendo
GameCube') are the same system as EmuDeck's folder; two folders for one system are one system in the
Library; a game in both is listed once and reported by the Check-up; keys saved under the old name
(checkouts, favourites, artwork info) still find their game. And .md files in Mega Drive folders are
games, not text."""
import json, os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/libnames"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L, libcheck as C, libmeta as M

checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


# 1. names
for name, want in [("DC", "dreamcast"), ("Dreamcast", "dreamcast"), ("Sega Dreamcast", "dreamcast"),
                   ("sega_dreamcast", "dreamcast"), ("MD", "genesis"), ("Mega Drive", "genesis"),
                   ("megadrive", "genesis"), ("SMS", "mastersystem"), ("Master System", "mastersystem"),
                   ("Nintendo GameCube", "gc"), ("Sony PlayStation 2", "ps2"), ("gameboy", "gb"),
                   ("Game Boy", "gb"), ("GG", "gamegear"), ("PCE", "pcengine"), ("Nintendo Wii U", "wiiu"),
                   ("ps2", "ps2"), ("Ports", "ports"), ("nintendo", "nintendo"), ("Favourites", "favourites")]:
    ok(L.canonical(name) == want, (name, L.canonical(name), want))
ok(L.rekey("dc/Game (USA).chd") == "dreamcast/Game (USA).chd" and L.rekey("snes/x.sfc") == "snes/x.sfc")
ok(L.rekey_dict({"dc/a": 1, "dreamcast/a": 2, "dc/b": 3}) == {"dreamcast/a": 2, "dreamcast/b": 3})
print("1. folder names: ok")

# 2. a library with 'Dreamcast' and 'DC', and a Mega Drive folder of .md files
lib = T / "NUC/Games"
def mk(rel, size=1000):
    p = lib / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(size))
mk("Dreamcast/Crazy Cab (USA).chd"); mk("Dreamcast/Space Ship (USA).chd")
mk("DC/Space Ship (USA).chd", 1200); mk("DC/Sword Tale (USA).gdi"); mk("DC/Sword Tale (USA) (Track 1).bin")
mk("megadrive/Hedgehog Run (USA).md"); mk("megadrive/Street Fight (Europe).md"); mk("megadrive/README.md", 200)
runner = cloudsync.RcloneRunner(shutil.which("rclone"), "", lambda m: None)
lb = L.Library(runner, str(lib), str(T / "home/Emulation/roms"), machine="deck", label="Deck")
lb.refresh()
dc = sorted(k for k in lb.items if k.startswith("dreamcast/"))
ok(dc == ["dreamcast/Crazy Cab (USA).chd", "dreamcast/Space Ship (USA).chd", "dreamcast/Sword Tale (USA).gdi"], dc)
ok(not any(k.startswith("dc/") for k in lb.items) and lb.lib_sysdirs["dreamcast"] == "Dreamcast", lb.lib_sysdirs)
ok(lb.items["dreamcast/Space Ship (USA).chd"].sysdir == "Dreamcast" and len(lb.shadowed) == 1
   and lb.shadowed[0].sysdir == "DC", [(i.sysdir, i.path) for i in lb.shadowed])
ok(lb.items["dreamcast/Sword Tale (USA).gdi"].sysdir == "DC", "each game remembers its own folder")
print("2. 'Dreamcast' + 'DC' = one system:", len(dc), "games; one in both folders")

# 3. the index cache keeps the same view
lb2 = L.Library(runner, str(lib), str(T / "home/Emulation/roms"), machine="deck", label="Deck")
ok(lb2.load_cache() and set(lb2.items) == set(lb.items) and len(lb2.shadowed) == 1)
print("3. cache: same view")

# 4. the Check-up: .md games aren't 'not games'; README.md is; the game in both folders is a duplicate
rep = C.check(lb.items.values(), lb.shadowed)
notgame = sorted(f.title for f in rep.findings if f.category == "notgame")
ok(notgame == ["README"], notgame)
dup = [f for f in rep.findings if f.category == "duplicate" and f.system == "dreamcast"]
ok(len(dup) == 1 and "'Dreamcast' and 'DC'" in dup[0].detail and dup[0].keys == ["dreamcast/Space Ship (USA).chd"],
   [(f.title, f.detail, f.keys) for f in dup])
ok(C.file_kind("Game.md", "genesis") == "game" and C.file_kind("notes.md", "snes") == "text"
   and C.file_kind("readme.md", "genesis") == "text")
print("4. Check-up:", notgame, "| duplicate:", dup[0].detail[:60], "…")

# 5. keys saved under 'dc/…' before still find their game
L._write_json(L.LOCAL_MANIFEST, {lb.location: {"dc/Sword Tale (USA).gdi": {"size": 2000}}})
ok("dreamcast/Sword Tale (USA).gdi" in lb.manifest())
(lib / ".library").mkdir(exist_ok=True)
(lib / ".library/library.json").write_text(json.dumps({"items": {"dc/Crazy Cab (USA).chd": {"fav": True}}}))
lb.load_shared()
ok(lb.is_favourite("dreamcast/Crazy Cab (USA).chd"))
ok(lb.set_favourite(["dreamcast/Space Ship (USA).chd"], True))
saved = json.loads((lib / ".library/library.json").read_text())["items"]
ok(set(saved) == {"dreamcast/Crazy Cab (USA).chd", "dreamcast/Space Ship (USA).chd"}, saved)
(lib / ".library/meta").mkdir(parents=True, exist_ok=True)
(lib / ".library/meta/dc.json").write_text(json.dumps({"dc/Sword Tale (USA).gdi": {"title": "Sword Tale"},
                                                      "dc/Space Ship (USA).chd": {"title": "old"}}))
(lib / ".library/meta/dreamcast.json").write_text(json.dumps({"dreamcast/Space Ship (USA).chd": {"title": "Space Ship"}}))
M.MEDIA_CACHE = T / "media"
store = M.MetaStore(lb)
store.load()
ok(store.get("dreamcast/Sword Tale (USA).gdi").get("title") == "Sword Tale", store.data.keys())
ok(store.get("dreamcast/Space Ship (USA).chd").get("title") == "Space Ship", "the canonical file wins")
store2 = M.MetaStore(lb)
ok(store2.load_local_copy() and store2.get("dreamcast/Sword Tale (USA).gdi").get("title") == "Sword Tale")
print("5. checkouts, favourites and artwork info saved under 'dc/' carry over")
print(f"{checks} checks passed")
