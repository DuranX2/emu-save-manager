"""Wii saves in Dolphin's NAND: listing game saves (system software skipped), banner.bin with every
icon frame, timing, back-and-forth and the "no copy" flag; Wii-style block counts; backing up to
and restoring from the backups folder (staged, verified, swapped in), replace / skip / cancel,
move and delete with safety copies."""
import json, os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/wiinandtest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
import wiinand as W
import savefixtures as F

wii = T / "Wii"
smg = wii / "title/00010000/524d4745/data"
F.wii_banner(smg / "banner.bin", icons=3, flags=0x10)
(smg / "GameData.bin").write_bytes(os.urandom(300000))
(smg / "sub").mkdir(); (smg / "sub/extra.dat").write_bytes(b"e" * 10)
ssb = wii / "title/00010000/52534245/data"
F.wii_banner(ssb / "banner.bin", "Super Smash Bros. Brawl", "Records", flags=0x01)
(ssb / "save.dat").write_bytes(b"s" * 5000)
nob = wii / "title/00010001/57414645/data"; nob.mkdir(parents=True); (nob / "x.bin").write_bytes(b"x")
(wii / "title/00000001/00000002/data").mkdir(parents=True)                # the System Menu
(wii / "title/00000001/00000002/data/setting.txt").write_bytes(b"s")
(wii / "title/00010002/48414141/data").mkdir(parents=True)                # a system channel
(wii / "title/00010002/48414141/data/x").write_bytes(b"s")
(wii / "title/00010000/52454545/data").mkdir(parents=True)                # empty: no save
bk = T / "backups"; saves_dir = bk / "Wii saves"

# 1. listing
nand = W.list_nand(wii)
assert [s.key for s in nand] == ["00010000-524D4745", "00010000-52534245", "00010001-57414645"], [s.key for s in nand]
g = nand[0]
assert (g.title, g.subtitle, g.game_code, g.type_name) == ("Super Mario Galaxy", "Mario: 3 stars", "RMGE", "Disc game")
assert [t for _i, t in g.frames] == [1, 2, 3] and g.bounce and not g.no_copy and g.banner.width == 192
assert g.n_files == 3 and g.blocks == 3 and g.modified is not None                   # ~334 KB = 3 blocks
assert nand[1].no_copy and nand[2].title == "" and nand[2].game_code == "WAFE" and nand[2].type_name == "Channel"
print("1. game saves listed with banner, icon frames, back-and-forth, no-copy flag, Wii blocks; system software skipped: ok")

# 2. back up (to the "SD card"): staged, verified, with info.json
rep = W.transfer(nand[:2], lambda s: W.backup_path(saves_dir, s), False, bk)
assert len(rep.done) == 2
b = saves_dir / "00010000-524D4745"
assert (b / "data/GameData.bin").read_bytes() == (smg / "GameData.bin").read_bytes() and (b / "data/sub/extra.dat").exists()
assert json.loads((b / "info.json").read_text())["title"] == "Super Mario Galaxy"
assert not list(saves_dir.glob("*/*.copying"))
backups = W.list_backups(saves_dir)
assert [s.key for s in backups] == ["00010000-524D4745", "00010000-52534245"] and backups[0].kind == "backup"
print("2. backing up copies the whole save folder (verified) into the backups, one per game: ok")

# 3. again: conflict -> skip / replace (old kept) / cancel
asked = []
rep = W.transfer(nand[:1], lambda s: W.backup_path(saves_dir, s), False, bk,
                 conflict=lambda s, old: asked.append(old.key) or "skip")
assert asked == ["00010000-524D4745"] and rep.skipped
(smg / "GameData.bin").write_bytes(b"newer" * 100)
rep = W.transfer(W.list_nand(wii)[:1], lambda s: W.backup_path(saves_dir, s), False, bk, conflict=lambda s, o: "replace")
assert rep.done and (b / "data/GameData.bin").read_bytes() == b"newer" * 100
kept = list(rep.safety.rglob("GameData.bin"))
assert len(kept) == 1 and len(kept[0].read_bytes()) == 300000
assert W.transfer(W.list_nand(wii)[:2], lambda s: W.backup_path(saves_dir, s), False, bk,
                  conflict=lambda s, o: "cancel").cancelled
print("3. a backup that's already there: skip, replace (the old one kept), or cancel: ok")

# 4. restore into a fresh NAND, then delete and move
wii2 = T / "Wii2"
rep = W.transfer(W.list_backups(saves_dir), lambda s: W.nand_path(wii2, s), False, bk)
assert len(rep.done) == 2 and [s.key for s in W.list_nand(wii2)] == ["00010000-524D4745", "00010000-52534245"]
assert not (wii2 / "title/00010000/524d4745/info.json").exists()              # backups' notes stay out of the NAND
rep = W.delete([s for s in W.list_nand(wii2) if s.lo == "52534245"], bk)
assert not (wii2 / "title/00010000/52534245/data").exists() and list(rep.safety.rglob("save.dat"))
rep = W.transfer([s for s in W.list_backups(saves_dir) if s.lo == "52534245"], lambda s: W.nand_path(wii2, s),
                 True, bk)
assert (wii2 / "title/00010000/52534245/data/save.dat").exists() and not (saves_dir / "00010000-52534245").exists()
assert W.transfer(W.list_nand(wii2)[:1], lambda s: W.nand_path(wii2, s), False, bk).refused[0][1] == "it's already there"
print("4. restore into Dolphin's NAND, delete (kept in the safety folder) and move: ok")

# 5. damaged banners don't break listing; safety keeps 20
F.wii_banner(T / "junk/banner.bin")
assert W.parse_banner(b"WIBN" + b"\0" * 20) is None and W.parse_banner(b"x" * 0x7000) is None
for k in range(25):
    d = T / f"t{k}/title/00010000/41414141/data"; d.mkdir(parents=True); (d / "f").write_bytes(b"x")
    W.delete(W.list_nand(T / f"t{k}"), bk)
assert len([d for d in (bk / "_wii_safety").iterdir() if d.is_dir()]) == 20
print("5. damaged banners are skipped quietly; the safety folder keeps the newest 20 changes: ok")
print("test_wiinand OK")
