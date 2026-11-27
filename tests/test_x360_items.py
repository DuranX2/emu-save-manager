"""Xbox 360 games in the ROM Library: Xbox Live Arcade / Games on Demand title-ID folders (packages
with no file extension) and extracted games (.xex) are one entry each, not one per file."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import romlibrary as L, libcheck

checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


E = []


def f(p, s=100):
    E.append((p, s, 0, False))


for i in range(40):                                     # an XBLA game with many package files
    f(f"xbox360/Braid (USA)/58410A1B/000D0000/{i:040X}")
f("xbox360/Braid (USA)/58410A1B/00000002/DLC0000000000000000000000000000000000001")
f("xbox360/Braid (USA)/58410A1B/000B0000/TU00000000000000000000000000000000000001")
f("xbox360/Shadow Game (USA)/584108FF/00007000/0123456789ABCDEF0123456789ABCDEF01234567")
for i in range(12):                                     # Games on Demand: package + .data folder
    f(f"xbox360/Shadow Game (USA)/584108FF/00007000/0123456789ABCDEF0123456789ABCDEF01234567.data/Data{i:04d}")
f("xbox360/XBLA/58410B00/000D0000/AAAA"); f("xbox360/XBLA/58410B01/000D0000/BBBB")   # a category
f("xbox360/58410C00/000D0000/CCCC")                     # a title-ID folder straight in xbox360/
f("xbox360/Halo 3 (USA).iso", 5000)
f("xbox360/Extracted Game (USA)/default.xex"); f("xbox360/Extracted Game (USA)/media/a.bin")
f("xbox360/Odd Game (USA)/game.xex")
for i in range(30):
    f(f"xbox360/Odd Game (USA)/data/{i}.pak")
f("ps2/Category/Not Hex/file.bin")                      # other systems unchanged
f("ps2/Category/Game (USA).iso")

items = {it.key: it for it in L.build_items(E)["xbox360"]}
ok(set(items) == {"xbox360/Braid (USA)", "xbox360/Shadow Game (USA)", "xbox360/XBLA/58410B00",
                  "xbox360/XBLA/58410B01", "xbox360/58410C00", "xbox360/Halo 3 (USA).iso",
                  "xbox360/Extracted Game (USA)", "xbox360/Odd Game (USA)"}, sorted(items))
ok(len(items["xbox360/Braid (USA)"].files) == 42 and items["xbox360/Braid (USA)"].is_dir)
ok(len(items["xbox360/Shadow Game (USA)"].files) == 13)
ok(len(items["xbox360/Odd Game (USA)"].files) == 31)
ok(items["xbox360/Braid (USA)"].title == "Braid (USA)")
ps2 = sorted(it.key for it in L.build_items(E)["ps2"])
ok(ps2 == ["ps2/Category/Game (USA).iso", "ps2/Category/Not Hex"], ps2)
print("1. Xbox 360 entries:", len(items), "(from", sum(1 for e in E if e[0].startswith("xbox360/")), "files)")

rep = libcheck.check(items.values())
ok(not [x for x in rep.findings if x.category == "notgame"], [(x.title, x.detail) for x in rep.findings])
print("2. Check-up finds no 'not games' among them")
print(f"{checks} checks passed")
