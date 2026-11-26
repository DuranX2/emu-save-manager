"""ROM Library check-up (libcheck.py): entries that aren't games, duplicates, formats that need
converting, space savers and the Wii U .wua rules. Uses a made-up library listing."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import romlibrary as rl, libcheck as L

checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


GB = 1024 ** 3
ID = "00050000101C9400"
UPD = "0005000E101C9400"
DLC = "0005000C101C9400"
E = []


def f(path, size=1000):
    E.append((path, size, 1700000000, False))


# PS1: games, a cover picture, a save and a database file
f("psx/Castle Quest (USA).chd", 500 * 1024 ** 2)
f("psx/Castle Quest (USA).png", 40000)
f("psx/Castle Quest (USA).srm", 8192)
f("psx/names.dat", 2000)
f("psx/Racer (USA).cue", 100); f("psx/Racer (USA) (Track 1).bin", 600 * 1024 ** 2)
f("psx/Racer (USA) (Track 2).bin", 30 * 1024 ** 2)
# PS2: an .iso, the same game as .chd too, a README and a .lily file
f("ps2/Space Game (USA).iso", 4 * GB)
f("ps2/Space Game (USA).chd", 2 * GB)
f("ps2/Big Adventure (Europe).iso", 3 * GB)
f("ps2/README", 300)
f("ps2/notes.lily", 300)
# PSP: a plugin's source folder, an .iso and a .cso
for n in ("main.c", "main.h", "Makefile", "exports.exp", "README.md"):
    f(f"psp/categories_lite-1.6/{n}", 900)
f("psp/Pocket Racer (USA).iso", GB)
f("psp/Puzzle Time (USA).cso", 200 * 1024 ** 2)
# Saturn and Xbox 360 archives; an extracted original Xbox game
f("saturn/Shrine Run (Japan).7z", 300 * 1024 ** 2)
f("saturn/Star Shot (USA).zip", 200 * 1024 ** 2)
f("saturn/Okay Game (USA).chd", 300 * 1024 ** 2)
f("xbox360/Arena (USA).rar", 6 * GB)
f("xbox/Halo Like (USA)/default.xbe", 3 * 1024 ** 2)
f("xbox/Halo Like (USA)/media/level1.map", 400 * 1024 ** 2)
f("xbox/Disc Game (USA).iso", 2 * GB)
# GameCube: .iso and .rvz; SNES zips (fine: cartridge systems load archives)
f("gc/Island Quest (USA).iso", 1400 * 1024 ** 2)
f("gc/Plumber Party (USA).rvz", 500 * 1024 ** 2)
f("snes/Platformer (USA).zip", 1024 ** 2)
# the same file in two category folders
f("gba/USA/Pocket Hero (USA).gba", 16 * 1024 ** 2)
f("gba/Favourites/Pocket Hero (USA).gba", 16 * 1024 ** 2)
# Wii U: one game as unpacked base + update + DLC folders; one as installer packages;
# one as .wux; one already .wua (with a stray .wua copy elsewhere); a Cemu copy
for part, tid, tag in (("code/game.rpx", ID, ""), ("content/data.pak", ID, ""), ("meta/meta.xml", ID, "")):
    f(f"wiiu/Kart Racer [{tid}]/{part}", 300 * 1024 ** 2)
f(f"wiiu/Kart Racer (UPDATE DATA) [{UPD}]/code/game.rpx", 50 * 1024 ** 2)
f(f"wiiu/Kart Racer (UPDATE DATA) [{UPD}]/meta/meta.xml", 1000)
f(f"wiiu/Kart Racer (DLC) (unpacked) [{DLC}]/content/dlc.pak", 100 * 1024 ** 2)
f(f"wiiu/Kart Racer (DLC) (unpacked) [{DLC}]/meta/meta.xml", 1000)
for n in ("title.tmd", "title.tik", "title.cert", "00000000.app", "00000001.app", "00000001.h3"):
    f(f"wiiu/DATA/USA/GAMES/Island Swim (USA)/{n}", 100 * 1024 ** 2)
f("wiiu/Zoo Party (USA).wux", 3 * GB)
f("wiiu/Sky Quest (USA).wua", 5 * GB)
f("wiiu/Backup/Sky Quest (USA).wua", 5 * GB)
f("ps2/Spare/Big Adventure (Europe).iso", 3 * GB)
for n in ("Cemu.exe", "SDL2.dll", "resources/en/cemu.mo", "gameProfiles/default/000500001010ec00.ini"):
    f(f"wiiu/DATA/EMULATORS/Cemu/BIN/{n}", 5 * 1024 ** 2)
# DOS keeps loose files: never "not a game"
f("dos/Old Game/GAME.EXE", 300000); f("dos/Old Game/README.TXT", 300)
# PICO-8 carts are pictures
f("pico8/tiny game.p8.png", 30000)
# an empty file
f("nes/Blank (USA).nes", 0)

items = [it for lst in rl.build_items(E).values() for it in lst]
rep = L.check(items)
by = rep.by_category()


def find(cat, sys_, title_part):
    return [x for x in by.get(cat, []) if x.system == sys_ and title_part.lower() in x.title.lower()]


# 1. not games
ok(find("notgame", "psx", "Castle Quest (USA)") and len(find("notgame", "psx", "Castle")) == 2,
   [(x.title, x.detail) for x in by["notgame"]])
pic = [x for x in find("notgame", "psx", "Castle") if "picture" in x.detail]
sav = [x for x in find("notgame", "psx", "Castle") if "save" in x.detail]
ok(pic and sav, "a cover picture and a save file")
ok(find("notgame", "psx", "names"), "a .dat file")
ok(find("notgame", "ps2", "README") and find("notgame", "ps2", "notes"), "README and .lily")
src = [x for x in by["notgame"] if x.system == "psp" and "categories_lite-1.6" in x.keys[0]]
ok(len(src) == 5 and all("source" in x.detail or "Only text" in x.detail for x in src),
   [(x.title, x.detail) for x in src])
ok(not find("notgame", "psp", "Pocket Racer"), "a game next to it is still a game")
emu = find("notgame", "wiiu", "BIN")
ok(emu and "emulator" in emu[0].detail, [(x.title, x.detail) for x in by["notgame"]])
ok(not find("notgame", "dos", "Old Game") and not find("notgame", "pico8", "tiny"), "DOS, PICO-8 are games")
ok(not find("notgame", "psx", "Racer"), "a cue/bin game is a game")
ok(find("notgame", "nes", "Blank") and "Empty" in find("notgame", "nes", "Blank")[0].detail, "empty file")
print("1. not games:", len(by["notgame"]))

# 2. duplicates: same game in two formats; the same file in two folders; two .wua copies
d = find("duplicate", "ps2", "Space Game")
ok(d and len(d[0].keys) == 2 and d[0].size == 4 * GB and ".chd" in d[0].detail and "Space Game (USA).chd" in d[0].detail,
   [(x.title, x.detail) for x in by.get("duplicate", [])])
ok(len(find("duplicate", "ps2", "Big Adventure")) == 1, "same game, one of them in a folder")
g = find("duplicate", "gba", "Pocket Hero")
ok(len(g) == 1 and g[0].size == 16 * 1024 ** 2 and g[0].title == "Pocket Hero",
   [(x.title, x.detail, x.size) for x in g])
ok([x for x in by["duplicate"] if x.system == "wiiu" and "wiiu/Backup" in x.keys],
   "two .wua copies are duplicates")
ok(not find("duplicate", "wiiu", "Kart Racer"), "Wii U layouts are left to the Wii U check")
print("2. duplicates:", len(by["duplicate"]))

# 3. needs converting
ok(find("convert", "saturn", "Shrine Run") and find("convert", "saturn", "Star Shot"), "Saturn archives")
ok(".7z" in find("convert", "saturn", "Shrine Run")[0].detail, "names the archive type")
ok(not find("convert", "saturn", "Okay Game"), "a Saturn .chd is fine")
ok(find("convert", "xbox360", "Arena"), "Xbox 360 .rar")
x = find("convert", "xbox", "Halo Like")
ok(x and "XISO" in x[0].detail, "extracted Xbox folder")
ok(not find("convert", "xbox", "Disc Game"), "Xbox .iso is fine")
ok(not find("convert", "snes", "Platformer"), "SNES zips are fine")
print("3. needs converting:", len(by["convert"]))

# 4. space savers
ba = find("space", "ps2", "Big Adventure")
ok(len(ba) == 2 and any("ps2/Spare" in x.keys for x in ba), "a folder holding one game is named after it")
ok( find("space", "ps2", "Space Game (USA)"), "PS2 .iso -> CHD")
ok(find("space", "psp", "Pocket Racer") and not find("space", "psp", "Puzzle"), "PSP .iso, not .cso")
ok(find("space", "gc", "Island Quest") and not find("space", "gc", "Plumber"), "GC .iso, not .rvz")
ok(find("space", "psx", "Racer") and not find("space", "psx", "Castle"), "PS1 cue/bin -> CHD, .chd fine")
print("4. space savers:", len(by["space"]))

# 5. Wii U
w = {x.title: x for x in by.get("wiiu", [])}
ok(set(w) == {"Kart Racer", "Island Swim", "Zoo Party"}, list(w))
k = w["Kart Racer"]
ok(len(k.keys) == 3 and "unpacked" in k.detail and "update and dlc" in k.detail.lower(), k.detail)
ok("installer" in w["Island Swim"].detail and "Install game title" in w["Island Swim"].detail, w["Island Swim"].detail)
ok(".wux" in w["Zoo Party"].detail, w["Zoo Party"].detail)
ok(k.size == 3 * 300 * 1024 ** 2 + 50 * 1024 ** 2 + 1000 + 100 * 1024 ** 2 + 1000, k.size)
# a .wua plus leftover folders for the same game
E2 = [(f"wiiu/Kart Racer [{ID}].wua", 2 * GB, 0, False),
      (f"wiiu/Kart Racer [{ID}]/code/game.rpx", GB, 0, False)]
r2 = L.check([it for lst in rl.build_items(E2).values() for it in lst])
ok(len(r2.findings) == 1 and "already" in r2.findings[0].detail, [(x.title, x.detail) for x in r2.findings])
print("5. Wii U:", sorted(w))

# 6. the text report and summary
t = rep.text()
ok("== Not games" in t and "== Wii U: not a .wua yet" in t and "wiiu/Kart Racer" in t, t[:400])
ok(L.summary(rep).startswith(f"Checked {len(items)} entries in "), L.summary(rep))
ok("nothing to report" in L.summary(L.check([])), L.summary(L.check([])))
ok("Nothing to report" in L.check([]).text())
print("6. report:", L.summary(rep))
print(f"{checks} checks passed")
